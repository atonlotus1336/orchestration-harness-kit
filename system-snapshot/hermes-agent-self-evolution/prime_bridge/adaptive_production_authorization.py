"""Explicit host-owner production authorization, never synthetic qualification.

This opt-in path waives empirical rollout gates, not execution safety controls.
The signed authority is host-local, identity-scoped, expiring and revocable by
the existing policy kill switch. Shipped defaults and qualification history are
never changed. Runtime callers must supply their observed identity.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from .adaptive_parallel_activation import (
    ROOT, ACTIVATION_LAYERS, LAYER_CAPABILITIES, _atomic, _utc, source_binding,
)

STATE = 'production_authorized'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def signature(payload, key):
    return hmac.new(key, json.dumps(payload, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()


def implementation_binding():
    files = [Path(__file__), ROOT / 'prime_bridge/adaptive_switch.py',
             ROOT / 'prime_bridge/adaptive_runtime_refresh.py',
             ROOT / 'prime_bridge/mcp_server.py',
             ROOT / 'prime_bridge/adaptive_review/controller.py',
             ROOT / 'prime_bridge/adaptive_review/careers.py']
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def host_key():
    """Read the existing local authority without copying or printing credentials."""
    value = os.environ.get('LAPART_QUALIFICATION_HMAC_KEY')
    if not value and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as registry:
                value = winreg.QueryValueEx(registry, 'LAPART_QUALIFICATION_HMAC_KEY')[0]
        except OSError:
            pass
    if not value and os.name != 'nt':
        # The configured Workspace profile is the existing owner, not a new key.
        env = Path.home() / '.hermes/profiles/workspace/.env'
        if env.is_file():
            for line in env.read_text().splitlines():
                name, sep, candidate = line.partition('=')
                if sep and name == 'LAPART_QUALIFICATION_HMAC_KEY':
                    value = candidate
                    break
    return value.encode() if isinstance(value, str) and len(value) >= 16 else None


def review_identity(config):
    return dict(profile=os.environ.get('LAPART_REVIEW_PROFILE', 'workspace'),
                model_route_sha256=digest(config['provider']), workload_class='coding')


def validate_identity(item, *, harness=None):
    expected = {'layer', 'harness', 'profile', 'model_route_sha256',
                'workload_class', 's_pipeline'}
    valid = (isinstance(item, dict) and set(item) == expected
             and item['harness'] in {'codex', 'hermes'}
             and (harness is None or item['harness'] == harness)
             and item['layer'] in ACTIVATION_LAYERS
             and isinstance(item['profile'], str) and bool(item['profile'])
             and isinstance(item['workload_class'], str) and bool(item['workload_class'])
             and isinstance(item['model_route_sha256'], str)
             and re.fullmatch('[0-9a-f]{64}', item['model_route_sha256']) is not None)
    if valid:
        valid = (item['s_pipeline'] is None if item['layer'] == 'execution_parallelism'
                 else item['s_pipeline'] in {'S0', 'S1', 'S2', 'S3', 'S4'})
    if not valid:
        raise ValueError('invalid owner-authorized identity')
    return dict(item)


def complete_identity_matrix(identities, harness):
    rows = [validate_identity(item, harness=harness) for item in identities]
    expected = {('execution_parallelism', None)}
    expected.update((layer, pipeline) for layer in ACTIVATION_LAYERS[1:]
                    for pipeline in ('S0', 'S1', 'S2', 'S3', 'S4'))
    observed = {(item['layer'], item['s_pipeline']) for item in rows}
    if observed != expected or len(rows) != len(expected):
        raise ValueError(f'{harness} requires a complete identity matrix')
    return sorted(rows, key=lambda item: (item['layer'], item['s_pipeline'] or ''))


def build_harness_payload(*, policy_path, harness, identities, authorization,
                          expires_at, now):
    if not authorization:
        raise ValueError('explicit authorization required')
    if not 0 < (_utc(expires_at) - now).total_seconds() <= 31 * 86400:
        raise ValueError('owner authorization expiry must be within 31 days')
    return {
        'schema_version': '2.0', 'kind': STATE, 'harness': harness,
        'authorization': authorization,
        'issued_at': now.isoformat().replace('+00:00', 'Z'),
        'expires_at': expires_at,
        'source_binding': source_binding(Path(policy_path)),
        'implementation_binding': implementation_binding(),
        'identities': complete_identity_matrix(identities, harness),
        'measured_qualification': 'not_claimed',
    }


def envelope_harnesses(envelope):
    if (isinstance(envelope, dict)
            and set(envelope) == {'schema_version', 'kind', 'authorizations'}
            and envelope.get('schema_version') == '2.0'
            and envelope.get('kind') == STATE):
        values = envelope.get('authorizations')
        if isinstance(values, dict) and set(values) <= {'codex', 'hermes'}:
            return tuple(sorted(values))
    if isinstance(envelope, dict) and set(envelope) == {'payload', 'signatures'}:
        identities = envelope.get('payload', {}).get('identities', [])
        return tuple(sorted({item.get('harness') for item in identities
                            if isinstance(item, dict)
                            and item.get('harness') in {'codex', 'hermes'}}))
    return ()


def _v2_payload(envelope, harness, path, key, now):
    if (set(envelope) != {'schema_version', 'kind', 'authorizations'}
            or envelope['schema_version'] != '2.0' or envelope['kind'] != STATE
            or not isinstance(envelope['authorizations'], dict)
            or set(envelope['authorizations']) - {'codex', 'hermes'}):
        return None
    entry = envelope['authorizations'].get(harness)
    if not isinstance(entry, dict) or set(entry) != {'payload', 'signatures'}:
        return None
    payload = entry['payload']
    fields = {'schema_version', 'kind', 'harness', 'authorization', 'issued_at',
              'expires_at', 'source_binding', 'implementation_binding',
              'identities', 'measured_qualification'}
    if (not isinstance(payload, dict) or set(payload) != fields
            or payload['schema_version'] != '2.0' or payload['kind'] != STATE
            or payload['harness'] != harness
            or payload['measured_qualification'] != 'not_claimed'
            or not isinstance(payload['authorization'], str)
            or not payload['authorization']):
        return None
    if (not _utc(payload['issued_at']) <= now < _utc(payload['expires_at'])
            or payload['source_binding'] != source_binding(path)
            or payload['implementation_binding'] != implementation_binding()):
        return None
    signatures = entry['signatures']
    if not isinstance(signatures, list) or not any(
            isinstance(item, str) and hmac.compare_digest(item, signature(payload, key))
            for item in signatures):
        return None
    return payload


def _verify_v2(*, path, policy, envelope, layer, harness, profile,
               model_route_sha256, workload_class, s_pipeline, key, now):
    payload = _v2_payload(envelope, harness, path, key, now)
    if payload is None:
        return None
    identities = complete_identity_matrix(payload['identities'], harness)
    reference = digest(envelope)
    expiries = [_utc(value['payload']['expires_at'])
                for value in envelope['authorizations'].values()]
    grant = policy['grants'][layer]
    if (grant['enabled'] is not True or grant['state'] != STATE
            or grant['qualification_reference'] != reference
            or _utc(grant['expiry']) != min(expiries)):
        return None
    identity = {'layer': layer, 'harness': harness, 'profile': profile,
                'model_route_sha256': model_route_sha256,
                'workload_class': workload_class, 's_pipeline': s_pipeline}
    if identity not in identities:
        return None
    ceiling = (policy['execution']['qualified_ceiling']
               if layer == 'execution_parallelism'
               else policy['reviewer']['qualified_ceiling']
               if layer == 'reviewer_concurrency' else 1)
    return {**identity, 'qualification_state': STATE,
            'measured_qualification': 'not_claimed', 'layer_ceiling': ceiling,
            'expires_at': payload['expires_at'],
            'capability': LAYER_CAPABILITIES[layer],
            'authorization_reference': reference}


def verify(*, policy_path, layer, harness, profile, model_route_sha256,
           workload_class, s_pipeline, key, now):
    """Return owner authority only; any missing/altered binding is serial fallback."""
    try:
        if not isinstance(key, bytes) or len(key) < 16:
            return None
        path = Path(policy_path)
        policy = json.loads(path.read_text())
        if policy['activation_state'] != 'production':
            return None
        envelope = json.loads((path.parent / 'production-authorization.json').read_text())
        if envelope.get('schema_version') == '2.0':
            return _verify_v2(
                path=path, policy=policy, envelope=envelope, layer=layer,
                harness=harness, profile=profile,
                model_route_sha256=model_route_sha256,
                workload_class=workload_class, s_pipeline=s_pipeline,
                key=key, now=now,
            )
        if set(envelope) != {'payload', 'signatures'}:
            return None
        payload = envelope['payload']
        if set(payload) != {'schema_version', 'kind', 'authorization', 'issued_at',
                           'expires_at', 'source_binding', 'implementation_binding',
                           'identities', 'measured_qualification'}:
            return None
        if (payload['schema_version'] != '1.0' or payload['kind'] != STATE
                or payload['measured_qualification'] != 'not_claimed'
                or not isinstance(payload['authorization'], str) or not payload['authorization']
                or not _utc(payload['issued_at']) <= now < _utc(payload['expires_at'])
                or payload['source_binding'] != source_binding(path)
                or payload['implementation_binding'] != implementation_binding()
                or not isinstance(envelope['signatures'], list)
                or not any(isinstance(s, str) and hmac.compare_digest(s, signature(payload, key))
                           for s in envelope['signatures'])):
            return None
        grant = policy['grants'][layer]
        reference = digest(payload)
        if (grant['enabled'] is not True or grant['state'] != STATE
                or grant['qualification_reference'] != reference
                or grant['expiry'] != payload['expires_at']):
            return None
        ceiling = (policy['execution']['qualified_ceiling'] if layer == 'execution_parallelism'
                   else policy['reviewer']['qualified_ceiling'] if layer == 'reviewer_concurrency' else 1)
        identity = dict(layer=layer, harness=harness, profile=profile,
                        model_route_sha256=model_route_sha256, workload_class=workload_class,
                        s_pipeline=s_pipeline)
        if identity not in payload['identities']:
            return None
        return {**identity, 'qualification_state': STATE, 'measured_qualification': 'not_claimed',
                'layer_ceiling': ceiling, 'expires_at': payload['expires_at'],
                'capability': LAYER_CAPABILITIES[layer], 'authorization_reference': reference}
    except (KeyError, OSError, TypeError, ValueError):
        return None


def activate(*, policy_path, identities, keys, authorization, expires_at, now=None):
    """Administrative entry point. Requires explicit user authorization at callsite."""
    now = now or datetime.now(timezone.utc)
    if not authorization or not keys or any(not isinstance(k, bytes) or len(k) < 16 for k in keys):
        raise ValueError('explicit authorization and existing signing authority required')
    if not 0 < (_utc(expires_at) - now).total_seconds() <= 31 * 86400:
        raise ValueError('owner authorization expiry must be within 31 days')
    if not identities or len({digest(i) for i in identities}) != len(identities):
        raise ValueError('nonempty unique runtime identities required')
    for item in identities:
        validate_identity(item)
    path = Path(policy_path)
    policy = json.loads(path.read_text())
    prior = json.loads(json.dumps(policy))
    enabled = {i['layer'] for i in identities}
    if ({'temperament', 'career_shadow_pass'} & enabled and 'career_kernel' not in enabled
            or 'career_kernel' in enabled and 'reviewer_concurrency' not in enabled):
        raise ValueError('career activation dependencies required')
    policy['activation_state'] = 'production'
    for name, ceiling in [('execution', 8), ('reviewer', 4)]:
        active = ('execution_parallelism' if name == 'execution' else 'reviewer_concurrency') in enabled
        policy[name].update(enabled=active, qualification_state=STATE if active else 'disabled',
                            initial_ceiling=2 if active else 0, qualified_ceiling=ceiling if active else 0,
                            desired_ceiling=ceiling)
    for layer, grant in policy['grants'].items():
        grant.update(enabled=layer in enabled, state=STATE if layer in enabled else 'disabled',
                     qualification_reference=None, expiry=expires_at if layer in enabled else None)
    # New policy fails closed until the matching signed envelope is installed.
    _atomic(path.parent / 'receipts' / f'pre-owner-activation-{digest(prior)}.json', prior)
    _atomic(path, policy)
    payload = dict(schema_version='1.0', kind=STATE, authorization=authorization,
                   issued_at=now.isoformat().replace('+00:00', 'Z'), expires_at=expires_at,
                   source_binding=source_binding(path), implementation_binding=implementation_binding(),
                   identities=identities, measured_qualification='not_claimed')
    envelope = dict(payload=payload, signatures=[signature(payload, key) for key in keys])
    for grant in policy['grants'].values():
        if grant['enabled']:
            grant['qualification_reference'] = digest(payload)
    _atomic(path.parent / 'production-authorization.json', envelope)
    _atomic(path, policy)
    _atomic(path.parent / 'receipts' / f'owner-activation-{digest(payload)}.json', envelope)
    return payload


def career_authorizations(catalog, record):
    """Keep frozen compatibility/obligations; authorize no new careers or reviewers."""
    from .adaptive_review.catalog import CAREER_KERNEL_VERSION
    result = []
    for reviewer in catalog.reviewers.values():
        candidates = {career for obligation in reviewer.obligations
                      for career in catalog.compatibility.get(obligation, ())}
        for career in sorted(candidates):
            result.append(dict(reviewer_id=reviewer.reviewer_id, career_id=career,
                covered_obligations=list(reviewer.obligations), state=STATE,
                career_kernel_version=CAREER_KERNEL_VERSION,
                model_route=record['model_route_sha256'], workload_class=record['workload_class'],
                layer_configuration='career', expires_at_epoch=int(_utc(record['expires_at']).timestamp()),
                enabled=True))
    return result
