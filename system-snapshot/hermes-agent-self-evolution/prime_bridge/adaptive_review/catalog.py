"""Frozen reviewer catalog with deterministic trigger and overlap handling."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adapters import ReviewerCapability


class CatalogError(ValueError):
    pass


LENS_MATRIX_VERSION = '1.0'
CAREER_KERNEL_VERSION = '1.0'

_FROZEN_MANDATORY_REVIEWER_CONTRACTS: Mapping[str, tuple[str, tuple[str, ...], bool]] = {
    'ponytail-review': ('changed_scope_simplicity', ('behavioral_correctness',), False),
    'evidence-integrity-audit': ('evidence_integrity', ('evidence_integrity',), False),
    'improve-codebase-architecture': ('architecture', ('architecture_ownership',), False),
    'ponytail-audit': (
        'system_simplicity',
        ('behavioral_correctness', 'evidence_integrity'),
        True,
    ),
    'ponytail-debt': ('system_debt', ('debt_improvement',), True),
    'improve': ('system_improvement', ('architecture_ownership',), True),
}

_FROZEN_METHOD_TAGS: Mapping[str, str] = {
    'structuralist': 'invariants', 'phenomenologist': 'experience',
    'psychoanalyst': 'incentives', 'chaos_mathematician': 'dynamics',
    'linguist': 'semantics', 'game_theorist': 'incentives',
    'anthropologist': 'observation', 'physicist': 'constraints',
    'engineer': 'failure_analysis', 'evolutionary_biologist': 'adaptation',
    'mythologist': 'precedent', 'economist': 'allocation',
}

_FROZEN_COMPATIBILITY: Mapping[str, tuple[str, ...]] = {
    'behavioral_correctness': ('engineer', 'game_theorist', 'phenomenologist'),
    'evidence_integrity': ('anthropologist', 'physicist', 'engineer'),
    'architecture_ownership': ('structuralist', 'engineer', 'evolutionary_biologist'),
    'authority_security': ('game_theorist', 'engineer', 'linguist'),
    'lifecycle_recovery': ('engineer', 'chaos_mathematician', 'evolutionary_biologist'),
    'interface_compatibility': ('linguist', 'structuralist', 'engineer'),
    'operability_rollback': ('engineer', 'anthropologist', 'phenomenologist'),
    'goal_memory_provenance': ('structuralist', 'anthropologist', 'mythologist'),
    'debt_improvement': ('economist', 'engineer', 'evolutionary_biologist', 'psychoanalyst'),
}

_PROFILE_SIGNATURES = 'anthropologist=taurus/rooster/Harvest Moon;chaos_mathematician=aquarius/snake/Sturgeon Moon;economist=libra/dog/Beaver Moon;engineer=virgo/goat/Snow Moon;evolutionary_biologist=sagittarius/dragon/Buck Moon;game_theorist=aries/horse/Hunter\u0027s Moon;linguist=gemini/ox/Cold Moon;mythologist=cancer/tiger/Strawberry Moon;phenomenologist=pisces/rat/Pink Moon;physicist=leo/pig/Flower Moon;psychoanalyst=scorpio/rabbit/Wolf Moon;structuralist=capricorn/monkey/Worm Moon'
CANONICAL_PROFILE_METADATA = dict(
    item.split('=', 1) for item in _PROFILE_SIGNATURES.split(';')
)

MANDATORY_REVIEWER_CONTRACTS: Mapping[str, tuple[str, tuple[str, ...], bool]] = {
    'ponytail-review': ('changed_scope_simplicity', ('behavioral_correctness',), False),
    'evidence-integrity-audit': ('evidence_integrity', ('evidence_integrity',), False),
    'improve-codebase-architecture': ('architecture', ('architecture_ownership',), False),
    'ponytail-audit': (
        'system_simplicity',
        ('behavioral_correctness', 'evidence_integrity'),
        True,
    ),
    'ponytail-debt': ('system_debt', ('debt_improvement',), True),
    'improve': ('system_improvement', ('architecture_ownership',), True),
}

CANONICAL_METHOD_TAGS: Mapping[str, str] = {
    'structuralist': 'invariants', 'phenomenologist': 'experience',
    'psychoanalyst': 'incentives', 'chaos_mathematician': 'dynamics',
    'linguist': 'semantics', 'game_theorist': 'incentives',
    'anthropologist': 'observation', 'physicist': 'constraints',
    'engineer': 'failure_analysis', 'evolutionary_biologist': 'adaptation',
    'mythologist': 'precedent', 'economist': 'allocation',
}

CANONICAL_COMPATIBILITY: Mapping[str, tuple[str, ...]] = {
    'behavioral_correctness': ('engineer', 'game_theorist', 'phenomenologist'),
    'evidence_integrity': ('anthropologist', 'physicist', 'engineer'),
    'architecture_ownership': ('structuralist', 'engineer', 'evolutionary_biologist'),
    'authority_security': ('game_theorist', 'engineer', 'linguist'),
    'lifecycle_recovery': ('engineer', 'chaos_mathematician', 'evolutionary_biologist'),
    'interface_compatibility': ('linguist', 'structuralist', 'engineer'),
    'operability_rollback': ('engineer', 'anthropologist', 'phenomenologist'),
    'goal_memory_provenance': ('structuralist', 'anthropologist', 'mythologist'),
    'debt_improvement': ('economist', 'engineer', 'evolutionary_biologist', 'psychoanalyst'),
}


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    lens: str
    triggers: tuple[str, ...]
    skill_path: str
    skill_sha256: str
    overlaps: tuple[str, ...] = ()
    requires_confirmation: bool = False
    required: bool = True
    obligations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Career:
    career_id: str
    primary_method: str
    source_path: str
    source_sha256: str
    career_method: str
    lunar_method: str
    temperament_id: str
    temperament_strengths: tuple[str, ...]
    temperament_biases: tuple[str, ...]
    shadow_id: str
    shadow_countercheck: str
    shadow_dispositions: tuple[str, ...]
    qualification_state: str


@dataclass
class Catalog:
    reviewers: dict[str, Reviewer] = field(default_factory=dict)
    capabilities: dict[str, ReviewerCapability] = field(default_factory=dict)
    careers: dict[str, Career] = field(default_factory=dict)
    lens_matrix: dict[str, tuple[str, ...]] = field(default_factory=dict)
    compatibility: dict[str, tuple[str, ...]] = field(default_factory=dict)
    desired_distinct_careers: dict[str, int] = field(default_factory=dict)
    source_sha256: str | None = None

    def register(self, reviewer: Reviewer, capability: ReviewerCapability) -> None:
        if reviewer.reviewer_id in self.reviewers:
            raise CatalogError(f"duplicate reviewer: {reviewer.reviewer_id}")
        self.reviewers[reviewer.reviewer_id] = reviewer
        self.capabilities[reviewer.reviewer_id] = capability

    def triggered(self, triggers: Iterable[str]) -> tuple[Reviewer, ...]:
        requested = set(triggers)
        candidates = sorted(
            (item for item in self.reviewers.values() if requested.intersection(item.triggers)),
            key=lambda item: item.reviewer_id,
        )
        selected: list[Reviewer] = []
        selected_ids: set[str] = set()
        for reviewer in candidates:
            if selected_ids.intersection(reviewer.overlaps):
                continue
            selected.append(reviewer)
            selected_ids.add(reviewer.reviewer_id)
        return tuple(selected)

    def verify_skill(self, reviewer_id: str) -> bool:
        reviewer = self.reviewers[reviewer_id]
        path = Path(reviewer.skill_path)
        if not path.is_file() or len(reviewer.skill_sha256) != 64:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == reviewer.skill_sha256

    def capability_for(self, reviewer_id: str) -> ReviewerCapability:
        try:
            return self.capabilities[reviewer_id]
        except KeyError as exc:
            raise CatalogError(f"missing capability: {reviewer_id}") from exc

    def verify_career_source(self, career_id: str) -> bool:
        career = self.careers[career_id]
        path = Path(career.source_path)
        return (
            path.is_file()
            and len(career.source_sha256) == 64
            and hashlib.sha256(path.read_bytes()).hexdigest() == career.source_sha256
        )


def _closed(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise CatalogError(f"{label} contains unknown fields: {sorted(unknown)}")


def _strings(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
        or nonempty and not value
    ):
        raise CatalogError(f"{label} must be a unique string array")
    return tuple(value)


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{label} is required")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogError(f"unsafe {label}: {relative}")
    return relative


def _digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CatalogError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _verify_mandatory_reviewers(catalog: Catalog) -> None:
    for reviewer_id, expected in MANDATORY_REVIEWER_CONTRACTS.items():
        reviewer = catalog.reviewers.get(reviewer_id)
        if reviewer is None:
            raise CatalogError(f'mandatory reviewer is missing: {reviewer_id}')
        actual = (reviewer.lens, reviewer.obligations, reviewer.requires_confirmation)
        if actual != expected or not reviewer.required:
            raise CatalogError(f'mandatory reviewer contract changed: {reviewer_id}')


def _verify_career_projection(item: Mapping[str, Any]) -> None:
    career_id = item['career_id']
    signature = CANONICAL_PROFILE_METADATA.get(career_id)
    if signature is None:
        raise CatalogError(f'unknown canonical career: {career_id}')
    zodiac, shadow, lunar_method = signature.split('/', 2)
    expected_path = f'skills-shared/brainstorm-council/agents/bc-{career_id.replace("_", "-")}.md'
    actual = (
        item['source_path'], item['lunar_method'],
        item['temperament']['temperament_id'], item['career_shadow']['shadow_id'],
    )
    expected = (
        expected_path, lunar_method, f'western:{zodiac}', f'eastern:{shadow}',
    )
    if actual != expected or item['qualification_state'] != 'candidate':
        raise CatalogError(f'canonical career projection changed: {career_id}')


def load_frozen_catalog(
    config_path: Path,
    *,
    vault_root: Path,
    readable_files: Iterable[str],
) -> Catalog:
    """Load the canonical reviewer catalog and pin every skill by content hash."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    version = raw.get("schema_version")
    v2_fields = {
        "schema_version", "reviewers", "lens_matrix", "reviewer_bindings",
        "desired_distinct_careers", "method_tags", "compatibility", "careers",
    }
    if version == "1.0":
        _closed(raw, {"schema_version", "reviewers"}, "reviewer catalog")
        if set(raw) != {"schema_version", "reviewers"}:
            raise CatalogError("unsupported or malformed reviewer catalog")
        if not isinstance(raw.get("reviewers"), list):
            raise CatalogError("unsupported or malformed reviewer catalog")
        parsed_lenses = {
            item["lens"]: ("legacy reviewer contract",)
            for item in raw["reviewers"]
            if isinstance(item, Mapping) and isinstance(item.get("lens"), str)
        }
        bindings = {
            item["reviewer_id"]: [item["lens"]]
            for item in raw["reviewers"]
            if isinstance(item, Mapping)
            and isinstance(item.get("reviewer_id"), str)
            and isinstance(item.get("lens"), str)
        }
    elif version == "2.0":
        _closed(raw, v2_fields, "reviewer catalog")
        if set(raw) != v2_fields or not isinstance(raw.get("reviewers"), list) or not isinstance(raw.get("careers"), list):
            raise CatalogError("unsupported or malformed reviewer catalog")
        lens_matrix = raw["lens_matrix"]
        if not isinstance(lens_matrix, Mapping) or not lens_matrix:
            raise CatalogError("lens matrix must be a non-empty object")
        parsed_lenses = {}
        for lens_id, value in lens_matrix.items():
            _closed(value, {"evidence"}, f"lens {lens_id}")
            parsed_lenses[lens_id] = _strings(
                value.get("evidence"), f"lens {lens_id} evidence", nonempty=True,
            )
        bindings = raw["reviewer_bindings"]
        if not isinstance(bindings, Mapping):
            raise CatalogError("reviewer bindings must be an object")
    else:
        raise CatalogError("unsupported or malformed reviewer catalog")
    capability = ReviewerCapability(tuple(readable_files))
    catalog = Catalog(
        lens_matrix=parsed_lenses,
        source_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
    )
    reviewer_fields = {
        "reviewer_id", "lens", "triggers", "skill_path", "skill_sha256",
        "overlaps", "requires_confirmation", "required",
    }
    for item in raw["reviewers"]:
        _closed(item, reviewer_fields, "reviewer")
        required = {"reviewer_id", "lens", "triggers", "skill_path", "skill_sha256"}
        if not required.issubset(item):
            raise CatalogError("reviewer is missing required fields")
        if type(item.get("requires_confirmation", False)) is not bool or type(item.get("required", True)) is not bool:
            raise CatalogError("reviewer boolean field is malformed")
        relative = _safe_relative(item["skill_path"], "skill path")
        obligations = _strings(
            bindings.get(item["reviewer_id"]), "reviewer obligations", nonempty=True,
        )
        if any(obligation not in parsed_lenses for obligation in obligations):
            raise CatalogError("reviewer binding references an unknown lens")
        reviewer = Reviewer(
            reviewer_id=item["reviewer_id"],
            lens=item["lens"],
            triggers=_strings(
                item["triggers"], "reviewer triggers", nonempty=version == "2.0",
            ),
            skill_path=str((vault_root / relative).resolve()),
            skill_sha256=_digest(item["skill_sha256"], "skill_sha256"),
            overlaps=_strings(item.get("overlaps", []), "reviewer overlaps"),
            requires_confirmation=item.get("requires_confirmation", False),
            required=item.get("required", True),
            obligations=obligations,
        )
        catalog.register(reviewer, capability)
    if not catalog.reviewers:
        raise CatalogError("empty reviewer catalog")
    if set(bindings) != set(catalog.reviewers):
        raise CatalogError("reviewer bindings do not exactly cover reviewers")
    if version == "1.0":
        return catalog
    _verify_mandatory_reviewers(catalog)

    method_tags = raw["method_tags"]
    compatibility = raw["compatibility"]
    desired = raw["desired_distinct_careers"]
    if not isinstance(method_tags, Mapping) or not isinstance(compatibility, Mapping):
        raise CatalogError("career mappings must be objects")
    if desired != {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}:
        raise CatalogError("desired career counts must remain 0/1/2/3/4")
    career_fields = {
        "career_id", "primary_method", "source_path", "source_sha256", "career_method",
        "lunar_method", "temperament", "career_shadow", "qualification_state",
    }
    exact_careers = {
        "anthropologist", "chaos_mathematician", "economist", "engineer",
        "evolutionary_biologist", "game_theorist", "linguist", "mythologist",
        "phenomenologist", "physicist", "psychoanalyst", "structuralist",
    }
    for item in raw["careers"]:
        _closed(item, career_fields, "career")
        if set(item) != career_fields:
            raise CatalogError("career is missing required fields")
        temperament = item["temperament"]
        shadow = item["career_shadow"]
        _closed(temperament, {"temperament_id", "strengths", "biases"}, "temperament")
        _closed(shadow, {"shadow_id", "countercheck", "dispositions"}, "career_shadow")
        if set(temperament) != {"temperament_id", "strengths", "biases"} or set(shadow) != {
            "shadow_id", "countercheck", "dispositions",
        }:
            raise CatalogError("career overlay is missing required fields")
        career_id = item["career_id"]
        if career_id in catalog.careers:
            raise CatalogError(f"duplicate career: {career_id}")
        _verify_career_projection(item)
        source = _safe_relative(item["source_path"], "career source path")
        dispositions = _strings(
            shadow["dispositions"], "career shadow dispositions", nonempty=True,
        )
        if dispositions != ("accepted", "rejected", "unresolved"):
            raise CatalogError("career shadow dispositions are not the closed contract")
        if item["qualification_state"] not in {
            "disabled", "candidate", "canary_qualified", "production_qualified",
        }:
            raise CatalogError("career qualification state is invalid")
        for field_name in ("career_id", "primary_method", "career_method", "lunar_method"):
            if not isinstance(item[field_name], str) or not item[field_name]:
                raise CatalogError(f"{field_name} is required")
        if method_tags.get(career_id) != item["primary_method"]:
            raise CatalogError("career method tag mismatch")
        if not isinstance(temperament["temperament_id"], str) or not temperament["temperament_id"]:
            raise CatalogError("temperament_id is required")
        if not isinstance(shadow["shadow_id"], str) or not shadow["shadow_id"] or not isinstance(shadow["countercheck"], str) or not shadow["countercheck"]:
            raise CatalogError("career shadow identity and countercheck are required")
        catalog.careers[career_id] = Career(
            career_id=career_id,
            primary_method=item["primary_method"],
            source_path=str((vault_root / source).resolve()),
            source_sha256=_digest(item["source_sha256"], "career source_sha256"),
            career_method=item["career_method"],
            lunar_method=item["lunar_method"],
            temperament_id=temperament["temperament_id"],
            temperament_strengths=_strings(temperament["strengths"], "temperament strengths", nonempty=True),
            temperament_biases=_strings(temperament["biases"], "temperament biases", nonempty=True),
            shadow_id=shadow["shadow_id"],
            shadow_countercheck=shadow["countercheck"],
            shadow_dispositions=dispositions,
            qualification_state=item["qualification_state"],
        )
    if set(catalog.careers) != exact_careers or set(method_tags) != exact_careers:
        raise CatalogError("catalog must contain exactly twelve canonical careers")
    if dict(method_tags) != dict(CANONICAL_METHOD_TAGS):
        raise CatalogError("career method tags changed from the frozen contract")
    if set(compatibility) != set(parsed_lenses):
        raise CatalogError("career compatibility must exactly cover the lens matrix")
    catalog.compatibility = {
        lens: _strings(careers, f"compatibility {lens}", nonempty=True)
        for lens, careers in compatibility.items()
    }
    if catalog.compatibility != dict(CANONICAL_COMPATIBILITY):
        raise CatalogError("career compatibility changed from the frozen contract")
    if any(
        career not in catalog.careers
        for careers in catalog.compatibility.values()
        for career in careers
    ):
        raise CatalogError("career compatibility references an unknown career")
    catalog.desired_distinct_careers = dict(desired)
    if not all(catalog.verify_career_source(career_id) for career_id in catalog.careers):
        raise CatalogError("career source is missing or its content hash changed")
    return catalog
