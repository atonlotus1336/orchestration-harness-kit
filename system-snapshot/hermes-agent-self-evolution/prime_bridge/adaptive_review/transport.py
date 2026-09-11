"""Owned single-request JSON transport and one aggregate review budget.

No automatic retries, redirects, model tools, plugins, or source mounts.
The local HTTP worker is killed on deadline; remote cancellation depends on
provider behavior, so request output caps remain mandatory.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

class BudgetViolation(ValueError):
    pass

class ReviewBudgetTracker:
    def __init__(self, limits: Mapping[str, int], *, allow_parallel: bool = False):
        self.limits = dict(limits)
        for key in ('max_context_bytes','max_model_calls','max_estimated_input_tokens','max_tool_calls','max_elapsed_seconds','max_output_tokens','max_total_tokens'):
            if type(self.limits.get(key)) is not int or self.limits[key] < 0:
                raise BudgetViolation('invalid review budget: ' + key)
        self.started = time.monotonic()
        self.requests = self.input_units = self.output_units = self.total_units = self.estimated_input = 0
        self.complete = True
        self.failed = False
        self.tool_calls = 0
        self.last_finish_reason = None
        self.admitted_packets = None
        self.allow_parallel = allow_parallel
        self._lock = threading.RLock()
        self._inflight = {}

    def remaining(self):
        with self._lock:
            value = self.limits['max_elapsed_seconds'] - (time.monotonic() - self.started)
            if value <= 0 or self.failed:
                raise BudgetViolation('review deadline exhausted or prior request failed')
            return value

    def reserve(self, payload: bytes):
        with self._lock:
            self.remaining()
            worker_id = threading.get_ident()
            if worker_id in self._inflight:
                raise BudgetViolation('worker already has an unsettled request')
            if self._inflight and not self.allow_parallel:
                raise BudgetViolation('prior request usage is incomplete')
            # Text-only UTF-8 byte count is deliberately conservative, rather than
            # pretending a chars/4 estimate is an exact provider tokenizer count.
            estimate = len(payload)
            if estimate > self.limits['max_context_bytes']:
                raise BudgetViolation('complete request exceeds context byte budget')
            if self.estimated_input + estimate > self.limits['max_estimated_input_tokens']:
                raise BudgetViolation('aggregate estimated input budget exceeded')
            if self.requests >= self.limits['max_model_calls']:
                raise BudgetViolation('aggregate model request budget exceeded')
            reserved_input = sum(item[1] for item in self._inflight.values())
            reserved_output = sum(item[0] for item in self._inflight.values())
            output_cap = min(
                2048,
                self.limits['max_output_tokens'] - self.output_units - reserved_output,
                self.limits['max_total_tokens'] - self.total_units
                - reserved_input - reserved_output - estimate,
            )
            if output_cap < 1:
                raise BudgetViolation('aggregate output or total budget exhausted')
            if self.admitted_packets is not None:
                if self.allow_parallel:
                    remaining_packets = list(self.admitted_packets)
                    for _, _, reserved_payload in self._inflight.values():
                        if reserved_payload in remaining_packets:
                            remaining_packets.remove(reserved_payload)
                    if payload not in remaining_packets:
                        raise BudgetViolation('dispatched packet differs from admission')
                elif self.requests >= len(self.admitted_packets) or payload != self.admitted_packets[self.requests]:
                    raise BudgetViolation('dispatched packet differs from admission')
            admitted_cap = json.loads(payload)['max_tokens']
            if type(admitted_cap) is not int or not 1 <= admitted_cap <= output_cap:
                raise BudgetViolation('admitted output reservation is no longer available')
            output_cap = admitted_cap
            self.request_output_cap = output_cap
            self.requests += 1
            self.estimated_input += estimate
            self._inflight[worker_id] = (output_cap, estimate, payload)
            self.complete = False
            return output_cap

    def settle(self, usage):
        with self._lock:
            worker_id = threading.get_ident()
            reservation = self._inflight.get(worker_id)
            if reservation is None:
                raise BudgetViolation("no unsettled request")
            if not isinstance(usage, dict):
                raise BudgetViolation('provider usage missing')
            values = [usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
            if any(type(v) is not int or v < 0 for v in values) or values[0] + values[1] != values[2]:
                raise BudgetViolation('provider usage incomplete or inconsistent')
            self.input_units += values[0]; self.output_units += values[1]; self.total_units += values[2]
            del self._inflight[worker_id]
            self.complete = not self._inflight
            if values[1] > reservation[0]:
                raise BudgetViolation("provider exceeded the request output cap")
            if (self.input_units > self.limits['max_estimated_input_tokens']
                or self.output_units > self.limits['max_output_tokens']
                or self.total_units > self.limits['max_total_tokens']):
                raise BudgetViolation('observed aggregate usage exceeds budget')
        self.remaining()

    def public(self):
        with self._lock:
            return {'model_requests':self.requests,'tool_calls':self.tool_calls,
                    'input_units':self.input_units if self.complete else None,
                    'output_units':self.output_units if self.complete else None,
                    'total_units':self.total_units if self.complete else None,
                    'estimated_input_units':self.estimated_input,'usage_complete':self.complete,
                    'elapsed_ms':round((time.monotonic()-self.started)*1000),
                    'failed':self.failed,'automatic_retries':0,'last_finish_reason':self.last_finish_reason}

@dataclass(frozen=True)
class JsonReviewTransport:
    endpoint: str
    model: str
    credential_env: str | None = None
    reasoning_effort: str | None = None

    def validate(self):
        if self.reasoning_effort not in {None,"none","low","medium","high"}:
            raise BudgetViolation("invalid configured review reasoning effort")
        url = urlsplit(self.endpoint)
        if (url.username or url.password or url.fragment or url.query
            or url.scheme not in {'http','https'} or not url.hostname
            or url.scheme == 'http' and url.hostname not in {'127.0.0.1','localhost','::1'}):
            raise BudgetViolation('review endpoint must be HTTPS or local HTTP without embedded credentials')
        if not isinstance(self.model,str) or not self.model.strip():
            raise BudgetViolation('review model is required')
        if self.credential_env is not None and (not self.credential_env.isidentifier() or not os.environ.get(self.credential_env)):
            raise BudgetViolation('configured review credential is unavailable')

    def payload(self, skill: str, contents: Mapping[str,str], output_cap: int):
        request={'model':self.model,'messages':[
            {'role':'system','content':skill},
            {'role':'user','content':json.dumps(dict(contents),sort_keys=True)}],
            "max_tokens":output_cap,"stream":False}
        if self.reasoning_effort is not None:
            request["reasoning_effort"]=self.reasoning_effort
        return json.dumps(request,sort_keys=True,separators=(",",":")).encode()

    def run(self, skill: str, contents: Mapping[str,str], budget: ReviewBudgetTracker):
        self.validate()
        # Reserve using the widest output field, then send no larger payload.
        cap = budget.limits['max_output_tokens']
        if budget.admitted_packets is not None:
            if budget.allow_parallel:
                messages = json.loads(self.payload(skill, contents, 1))["messages"]
                matches = [
                    json.loads(packet) for packet in budget.admitted_packets
                    if json.loads(packet).get("messages") == messages
                ]
                if len(matches) != 1:
                    raise BudgetViolation('parallel reviewer packet identity is not unique')
                cap = matches[0]['max_tokens']
            else:
                if budget.requests >= len(budget.admitted_packets):
                    raise BudgetViolation('complete reviewer set already dispatched')
                cap = json.loads(budget.admitted_packets[budget.requests])['max_tokens']
        provisional=self.payload(skill,contents,cap)
        output_cap=budget.reserve(provisional)
        payload=self.payload(skill,contents,output_cap)
        envelope={'endpoint':self.endpoint,'payload':payload.decode(),'timeout':budget.remaining(),
                  'credential':os.environ.get(self.credential_env) if self.credential_env else None}
        process=None
        try:
            env={k:v for k,v in os.environ.items() if k in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','SSL_CERT_FILE','SSL_CERT_DIR'}}
            process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--worker'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            try:
                stdout,_=process.communicate(json.dumps(envelope).encode(),timeout=budget.remaining())
            except subprocess.TimeoutExpired:
                process.kill();process.communicate()
                raise BudgetViolation('review deadline exceeded; local transport terminated')
            if process.returncode != 0:
                raise BudgetViolation('review transport failed; usage unknown')
            result=json.loads(stdout)
            budget.settle(result.get('usage'))
            choice=result['choices'][0]
            reason=choice.get("finish_reason")
            budget.last_finish_reason = reason if reason in {None,"stop","length","content_filter","tool_calls"} else "other"
            if reason != 'stop':
                raise BudgetViolation('review response has no supported successful terminal stop')
            message=choice['message']
            if message.get('tool_calls') or message.get('function_call'):
                raise BudgetViolation('model tool calls are forbidden')
            findings=json.loads(message['content'])
            if not isinstance(findings,list) or any(not isinstance(f,dict) for f in findings):
                raise BudgetViolation('review response must be an array of findings')
            if any(any(k in f for k in ('patch','diff','replacement','apply')) for f in findings):
                raise BudgetViolation('patch payload is forbidden')
            return tuple(findings)
        except Exception:
            budget.failed=True
            if process is not None and process.poll() is None:
                process.kill();process.communicate()
            raise

def _worker():
    # Fixed trusted worker: exactly one HTTP request, no retry or redirect.
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            return None
    envelope=json.loads(sys.stdin.buffer.read(2_000_000))
    headers={'Content-Type':'application/json'}
    if envelope.get('credential'):
        headers['Authorization']='Bearer '+envelope['credential']
    request=urllib.request.Request(envelope['endpoint'],data=envelope['payload'].encode(),headers=headers,method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=envelope['timeout']) as response:
        body=response.read(1_048_577)
        if len(body)>1_048_576:
            raise ValueError('response exceeds byte cap')
    sys.stdout.buffer.write(body)

if __name__=='__main__':
    try:
        _worker()
    except Exception:
        # Do not put endpoint, prompts, provider response, or credentials in logs.
        raise SystemExit(1)
