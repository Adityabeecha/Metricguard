"""MetricGuard V6.2 Declarative Repository Configuration Core.

This module implements:
1. Typed configuration schema for metricguard.yml (Model B: Store owns governance identity; YAML owns repository CI bindings).
2. Deterministic semantic configuration canonicalization and hashing.
3. Provenance and trust tracking (trusted base vs untrusted candidate config vs local user).
4. Dual validation modes:
   - Schema-only validation (offline structural checks, duplicate detection, dbt manifest resource matching).
   - Governed validation (authoritative MetricGuard Store verification of workspace ownership and Metric UUID identity).
5. App URL validation with provenance-aware security.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
import urllib.parse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

# ---------------------------------------------------------------------------
# Constants & Enums
# ---------------------------------------------------------------------------

SUPPORTED_CONFIG_VERSION = 1

class ConfigProvenance(str, Enum):
    TRUSTED_BASE = "trusted_base"
    UNTRUSTED_CANDIDATE = "untrusted_candidate"
    LOCAL_USER = "local_user"


class ConfigErrorCode(str, Enum):
    MALFORMED_YAML = "MALFORMED_YAML"
    UNSUPPORTED_CONFIG_VERSION = "UNSUPPORTED_CONFIG_VERSION"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"
    FORBIDDEN_GOVERNANCE_FIELD = "FORBIDDEN_GOVERNANCE_FIELD"
    DUPLICATE_METRIC_BINDING = "DUPLICATE_METRIC_BINDING"
    DUPLICATE_DBT_MODEL_BINDING = "DUPLICATE_DBT_MODEL_BINDING"
    INVALID_CI_ACTION = "INVALID_CI_ACTION"
    INVALID_APP_URL = "INVALID_APP_URL"
    UNTRUSTED_APP_URL = "UNTRUSTED_APP_URL"
    DBT_RESOURCE_NOT_FOUND = "DBT_RESOURCE_NOT_FOUND"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    WORKSPACE_NOT_FOUND = "WORKSPACE_NOT_FOUND"
    METRIC_NOT_FOUND = "METRIC_NOT_FOUND"
    CROSS_WORKSPACE_METRIC = "CROSS_WORKSPACE_METRIC"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"
    INVALID_SNAPSHOT_BINDING = "INVALID_SNAPSHOT_BINDING"
    CONFIG_FILE_NOT_FOUND = "CONFIG_FILE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Domain Exceptions & Issue Reporting
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    code: str
    field_path: str
    message: str
    suggested_correction: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        res = {
            "code": self.code,
            "field_path": self.field_path,
            "message": self.message,
        }
        if self.suggested_correction:
            res["suggested_correction"] = self.suggested_correction
        return res

    def __str__(self) -> str:
        loc = f"{self.field_path}: " if self.field_path else ""
        sug = f" (Suggested: {self.suggested_correction})" if self.suggested_correction else ""
        return f"[{self.code}] {loc}{self.message}{sug}"


class ConfigValidationError(Exception):
    def __init__(self, issues: List[ValidationIssue]):
        self.issues = issues
        summary = "; ".join(str(i) for i in issues)
        super().__init__(summary)


# ---------------------------------------------------------------------------
# Pydantic Configuration Models (Strict Model B Enforced)
# ---------------------------------------------------------------------------

FORBIDDEN_METRIC_FIELDS = {
    "display_name",
    "name",
    "importance",
    "approved_baseline",
    "baseline_version_id",
    "status",
    "lifecycle_state",
    "threshold",
    "thresholds",
    "materiality",
    "materiality_policy",
}

FORBIDDEN_TOP_LEVEL_FIELDS = {
    "metrics_definitions",
    "definitions",
    "governance",
    "thresholds",
    "models",
}


class CIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["strict", "advisory"] = "strict"
    fail_on_refusals: bool = True


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ci: CIConfig = Field(default_factory=CIConfig)


class MetricBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(..., min_length=1, description="Canonical Store Metric UUID")
    dbt_model: str = Field(..., min_length=1, description="dbt resource unique_id (e.g. model.package.fct_orders)")
    ci_action: Literal["fail", "warn", "ignore"] = "fail"

    @model_validator(mode="before")
    @classmethod
    def check_forbidden_governance_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found_forbidden = set(data.keys()) & FORBIDDEN_METRIC_FIELDS
            if found_forbidden:
                forbidden_str = ", ".join(sorted(found_forbidden))
                raise ValueError(
                    f"MetricBinding cannot declare '{forbidden_str}'. "
                    "The MetricGuard Store is the authoritative source of truth for metric governance (Model B)."
                )
        return data


class SnapshotBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: Optional[str] = None
    file: Optional[str] = None
    contract_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_snapshot_resolution_strategy(self) -> SnapshotBinding:
        has_id = bool(self.snapshot_id and self.snapshot_id.strip())
        has_file_contract = bool(self.file and self.file.strip() and self.contract_id and self.contract_id.strip())

        if not has_id and not has_file_contract:
            raise ValueError(
                "snapshot binding must specify either 'snapshot_id' or both 'file' and 'contract_id'."
            )
        if has_id and has_file_contract:
            raise ValueError(
                "snapshot binding cannot specify both 'snapshot_id' and 'file'/'contract_id' simultaneously."
            )
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            return None
        return normalize_and_validate_app_url(clean, "app.base_url")


class MetricGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    workspace: str = Field(..., min_length=1, description="Workspace (project) ID in MetricGuard Store")
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    metrics: List[MetricBinding] = Field(default_factory=list)
    snapshot: Optional[SnapshotBinding] = None
    app: Optional[AppConfig] = None

    @model_validator(mode="before")
    @classmethod
    def check_version_and_top_level(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Check version first
            if "version" in data:
                v = data["version"]
                if v != SUPPORTED_CONFIG_VERSION:
                    raise ValueError(
                        f"UNSUPPORTED_CONFIG_VERSION: This config uses version {v}. "
                        f"This MetricGuard build supports version {SUPPORTED_CONFIG_VERSION}."
                    )
            else:
                raise ValueError("version: missing required field 'version'.")

            # Check forbidden top-level fields
            found_forbidden = set(data.keys()) & FORBIDDEN_TOP_LEVEL_FIELDS
            if found_forbidden:
                forbidden_str = ", ".join(sorted(found_forbidden))
                raise ValueError(
                    f"Top-level field '{forbidden_str}' is unsupported or reserved."
                )
        return data

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> MetricGuardConfig:
        seen_metric_ids: set[str] = set()
        seen_dbt_models: set[str] = set()

        for idx, binding in enumerate(self.metrics):
            if binding.metric_id in seen_metric_ids:
                raise ValueError(
                    f"metrics[{idx}].metric_id: Duplicate binding for metric_id '{binding.metric_id}'. "
                    "Each Metric can be bound to at most one dbt resource."
                )
            seen_metric_ids.add(binding.metric_id)

            if binding.dbt_model in seen_dbt_models:
                raise ValueError(
                    f"metrics[{idx}].dbt_model: Duplicate binding for dbt_model '{binding.dbt_model}'. "
                    "Each dbt resource can be bound to at most one Metric."
                )
            seen_dbt_models.add(binding.dbt_model)

        return self


# ---------------------------------------------------------------------------
# URL Security & Normalization
# ---------------------------------------------------------------------------

def normalize_and_validate_app_url(url_str: str, field_name: str = "app.base_url") -> str:
    """Validate that app URL uses http/https, has a valid host, and no query/fragments."""
    parsed = urllib.parse.urlparse(url_str)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"{field_name}: Invalid URL scheme '{parsed.scheme}'. Only http and https are permitted."
        )
    if not parsed.netloc:
        raise ValueError(f"{field_name}: Malformed URL, missing network host/domain in '{url_str}'.")
    if parsed.query or parsed.fragment:
        raise ValueError(
            f"{field_name}: URL must not contain query parameters or fragments."
        )
    # Strip trailing slash for consistency
    clean_path = parsed.path.rstrip("/")
    normalized = urllib.parse.urlunparse((
        parsed.scheme.lower(),
        parsed.netloc,
        clean_path,
        "", "", ""
    ))
    return normalized


# ---------------------------------------------------------------------------
# Canonical Semantic Hashing
# ---------------------------------------------------------------------------

def canonicalize_config_dict(config: MetricGuardConfig) -> dict[str, Any]:
    """
    Produce a deterministic, canonical dictionary invariant to formatting,
    comments, and input metric declaration order.
    """
    sorted_metrics = sorted(
        [
            {
                "metric_id": m.metric_id,
                "dbt_model": m.dbt_model,
                "ci_action": m.ci_action,
            }
            for m in config.metrics
        ],
        key=lambda item: (item["metric_id"], item["dbt_model"]),
    )

    snapshot_dict = None
    if config.snapshot:
        snapshot_dict = {
            "snapshot_id": config.snapshot.snapshot_id,
            "file": config.snapshot.file,
            "contract_id": config.snapshot.contract_id,
        }

    app_dict = None
    if config.app and config.app.base_url:
        app_dict = {"base_url": config.app.base_url}

    return {
        "version": config.version,
        "workspace": config.workspace,
        "defaults": {
            "ci": {
                "mode": config.defaults.ci.mode,
                "fail_on_refusals": config.defaults.ci.fail_on_refusals,
            }
        },
        "metrics": sorted_metrics,
        "snapshot": snapshot_dict,
        "app": app_dict,
    }


def compute_semantic_config_hash(config: MetricGuardConfig) -> str:
    """Compute a deterministic SHA-256 fingerprint over the canonical semantic structure."""
    canonical = canonicalize_config_dict(config)
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Loaded Config Container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoadedConfig:
    config: MetricGuardConfig
    raw_content: str
    config_hash: str
    provenance: ConfigProvenance
    source_path: Optional[str] = None
    effective_app_url: Optional[str] = None
    effective_app_url_trusted: bool = False


# ---------------------------------------------------------------------------
# Loader with Provenance & Security Resolution
# ---------------------------------------------------------------------------

def load_config_from_str(
    content: str,
    provenance: ConfigProvenance = ConfigProvenance.LOCAL_USER,
    source_path: Optional[str] = None,
) -> Tuple[Optional[LoadedConfig], List[ValidationIssue]]:
    """Parse and validate YAML content into a LoadedConfig container."""
    issues: List[ValidationIssue] = []

    # 1. Parse YAML
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        msg = f"YAML syntax error: {exc}"
        issues.append(ValidationIssue(code=ConfigErrorCode.MALFORMED_YAML.value, field_path="", message=msg))
        return None, issues

    if not isinstance(data, dict):
        issues.append(ValidationIssue(
            code=ConfigErrorCode.MALFORMED_YAML.value,
            field_path="",
            message="Configuration file must be a top-level YAML mapping/object."
        ))
        return None, issues

    # 2. Check version explicitly before general Pydantic schema validation
    version = data.get("version")
    if version is not None and version != SUPPORTED_CONFIG_VERSION:
        issues.append(ValidationIssue(
            code=ConfigErrorCode.UNSUPPORTED_CONFIG_VERSION.value,
            field_path="version",
            message=f"This config uses version {version}. This MetricGuard build supports version {SUPPORTED_CONFIG_VERSION}.",
            suggested_correction=f"Set 'version: {SUPPORTED_CONFIG_VERSION}'."
        ))
        return None, issues

    # 3. Pydantic validation
    try:
        config = MetricGuardConfig.model_validate(data)
    except Exception as exc:
        # Translate Pydantic validation errors into clean ValidationIssues
        from pydantic import ValidationError
        if isinstance(exc, ValidationError):
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                msg = err["msg"]
                # Clean up "Value error, " prefix if present
                if msg.startswith("Value error, "):
                    msg = msg[len("Value error, "):]

                code = ConfigErrorCode.INVALID_FIELD_TYPE.value
                err_type = err.get("type", "")
                if "missing" in msg.lower() or "required" in msg.lower() or err_type == "missing":
                    code = ConfigErrorCode.MISSING_REQUIRED_FIELD.value
                elif "duplicate binding" in msg.lower():
                    if "metric_id" in loc or "metric_id" in msg.lower():
                        code = ConfigErrorCode.DUPLICATE_METRIC_BINDING.value
                    else:
                        code = ConfigErrorCode.DUPLICATE_DBT_MODEL_BINDING.value
                elif "cannot declare" in msg.lower() or "unsupported or reserved" in msg.lower():
                    code = ConfigErrorCode.FORBIDDEN_GOVERNANCE_FIELD.value
                elif "ci_action" in loc or "ci_action" in msg.lower():
                    code = ConfigErrorCode.INVALID_CI_ACTION.value
                elif "unsupported_config_version" in msg.lower():
                    code = ConfigErrorCode.UNSUPPORTED_CONFIG_VERSION.value
                elif "url" in loc or "url" in msg.lower():
                    code = ConfigErrorCode.INVALID_APP_URL.value

                issues.append(ValidationIssue(code=code, field_path=loc, message=msg))
        else:
            issues.append(ValidationIssue(code=ConfigErrorCode.INVALID_FIELD_TYPE.value, field_path="", message=str(exc)))
        return None, issues

    # 4. Compute deterministic semantic hash
    config_hash = compute_semantic_config_hash(config)

    # 5. Resolve effective app URL with security provenance
    env_app_url = os.environ.get("METRICGUARD_APP_URL")
    effective_url = None
    effective_trusted = False

    if env_app_url and env_app_url.strip():
        try:
            effective_url = normalize_and_validate_app_url(env_app_url.strip(), "METRICGUARD_APP_URL")
            effective_trusted = True
        except ValueError as url_err:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.INVALID_APP_URL.value,
                field_path="METRICGUARD_APP_URL",
                message=str(url_err)
            ))
    elif config.app and config.app.base_url:
        if provenance == ConfigProvenance.TRUSTED_BASE or provenance == ConfigProvenance.LOCAL_USER:
            effective_url = config.app.base_url
            effective_trusted = True
        elif provenance == ConfigProvenance.UNTRUSTED_CANDIDATE:
            # Candidate PR cannot introduce untrusted deep link base URLs
            effective_url = config.app.base_url
            effective_trusted = False

    loaded = LoadedConfig(
        config=config,
        raw_content=content,
        config_hash=config_hash,
        provenance=provenance,
        source_path=source_path,
        effective_app_url=effective_url,
        effective_app_url_trusted=effective_trusted,
    )

    return loaded, issues


def load_config_from_file(
    file_path: Path | str,
    provenance: ConfigProvenance = ConfigProvenance.LOCAL_USER,
) -> Tuple[Optional[LoadedConfig], List[ValidationIssue]]:
    path = Path(file_path)
    if not path.is_file():
        return None, [
            ValidationIssue(
                code=ConfigErrorCode.CONFIG_FILE_NOT_FOUND.value,
                field_path="",
                message=f"Configuration file not found at '{path}'.",
                suggested_correction="Verify the file path or create metricguard.yml."
            )
        ]
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, [
            ValidationIssue(
                code=ConfigErrorCode.MALFORMED_YAML.value,
                field_path="",
                message=f"Could not read configuration file: {exc}"
            )
        ]
    return load_config_from_str(content, provenance=provenance, source_path=str(path.resolve()))


# ---------------------------------------------------------------------------
# Validation Engine: Schema-Only and Governed
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    is_valid: bool
    mode: Literal["schema_only", "governed"]
    loaded_config: Optional[LoadedConfig] = None
    issues: List[ValidationIssue] = field(default_factory=list)
    metrics_verified: int = 0
    dbt_resources_verified: int = 0
    store_connected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "mode": self.mode,
            "config_hash": self.loaded_config.config_hash if self.loaded_config else None,
            "workspace": self.loaded_config.config.workspace if self.loaded_config else None,
            "metrics_verified": self.metrics_verified,
            "dbt_resources_verified": self.dbt_resources_verified,
            "store_connected": self.store_connected,
            "issues": [i.to_dict() for i in self.issues],
        }


def validate_config(
    config_source: Path | str,
    manifest_path: Optional[Path | str] = None,
    schema_only: bool = False,
    database_url: Optional[str] = None,
    provenance: ConfigProvenance = ConfigProvenance.LOCAL_USER,
) -> ValidationResult:
    """
    Validate a metricguard.yml configuration.

    Modes:
    - schema_only=True: Structural, syntactic, duplicate, and manifest resource validation only.
    - schema_only=False (default): Connects to authoritative MetricGuard Store to verify
      workspace existence, Metric UUID validity, and absence of cross-workspace bindings.
    """
    mode_str: Literal["schema_only", "governed"] = "schema_only" if schema_only else "governed"

    # 1. Load config
    is_path = isinstance(config_source, Path) or (
        isinstance(config_source, str)
        and "\n" not in config_source
        and (
            os.path.exists(config_source)
            or config_source.endswith((".yml", ".yaml"))
            or not (":" in config_source and " " in config_source)
        )
    )
    if is_path:
        loaded, issues = load_config_from_file(config_source, provenance=provenance)
    else:
        loaded, issues = load_config_from_str(str(config_source), provenance=provenance)

    if issues or not loaded:
        return ValidationResult(
            is_valid=False,
            mode=mode_str,
            loaded_config=loaded,
            issues=issues,
        )

    config = loaded.config

    # 2. Manifest validation (if provided)
    dbt_verified_count = 0
    if manifest_path:
        mpath = Path(manifest_path)
        if not mpath.is_file():
            issues.append(ValidationIssue(
                code=ConfigErrorCode.INVALID_MANIFEST.value,
                field_path="manifest",
                message=f"dbt manifest file not found at '{mpath}'."
            ))
        else:
            try:
                manifest_bytes = mpath.read_bytes()
                manifest_json = json.loads(manifest_bytes.decode("utf-8"))
                nodes = manifest_json.get("nodes", {})
                if not isinstance(nodes, dict):
                    issues.append(ValidationIssue(
                        code=ConfigErrorCode.INVALID_MANIFEST.value,
                        field_path="manifest.nodes",
                        message="dbt manifest 'nodes' must be a dictionary."
                    ))
                else:
                    for idx, binding in enumerate(config.metrics):
                        if binding.dbt_model not in nodes:
                            issues.append(ValidationIssue(
                                code=ConfigErrorCode.DBT_RESOURCE_NOT_FOUND.value,
                                field_path=f"metrics[{idx}].dbt_model",
                                message=f"dbt resource '{binding.dbt_model}' not found in manifest.",
                                suggested_correction="Check model name and ensure dbt compile/build has generated target/manifest.json."
                            ))
                        else:
                            dbt_verified_count += 1
            except Exception as exc:
                issues.append(ValidationIssue(
                    code=ConfigErrorCode.INVALID_MANIFEST.value,
                    field_path="manifest",
                    message=f"Failed to parse dbt manifest: {exc}"
                ))

    # If schema-only, stop here
    if schema_only:
        return ValidationResult(
            is_valid=len(issues) == 0,
            mode="schema_only",
            loaded_config=loaded,
            issues=issues,
            metrics_verified=len(config.metrics) if not issues else 0,
            dbt_resources_verified=dbt_verified_count,
            store_connected=False,
        )

    # 3. Governed Validation against Authoritative Store
    metrics_verified_count = 0
    store_connected = False

    db_url = database_url or os.environ.get("METRICGUARD_DATABASE_URL")
    if not db_url:
        data_dir = Path(os.environ.get("METRICGUARD_DATA_DIR", "data"))
        db_path = data_dir / "metricguard.db"
        db_url = f"sqlite:///{db_path}"

    try:
        from services.api.store import Store, DomainError
        from sqlalchemy import select, create_engine
        from sqlalchemy.orm import sessionmaker

        # Test connection
        store = Store(db_url)
        store_connected = True

        # A. Verify workspace (project) exists
        try:
            project = store.get_project(config.workspace)
        except DomainError:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.WORKSPACE_NOT_FOUND.value,
                field_path="workspace",
                message=f"Workspace '{config.workspace}' does not exist in MetricGuard Store.",
                suggested_correction="Create the workspace first in MetricGuard Studio or check the workspace slug."
            ))
            return ValidationResult(
                is_valid=False,
                mode="governed",
                loaded_config=loaded,
                issues=issues,
                metrics_verified=0,
                dbt_resources_verified=dbt_verified_count,
                store_connected=True,
            )

        # B. Verify Metric IDs in workspace
        for idx, binding in enumerate(config.metrics):
            mid = binding.metric_id
            try:
                # First check in declared workspace
                m = store.get_metric(mid, project_id=config.workspace)
                metrics_verified_count += 1
            except DomainError:
                # Check if it exists in another workspace
                with store.engine.connect() as conn:
                    from services.api.store import metrics as metrics_tbl
                    other_row = conn.execute(
                        select(metrics_tbl).where(metrics_tbl.c.id == mid)
                    ).mappings().first()

                    if other_row:
                        other_ws = other_row["project_id"]
                        issues.append(ValidationIssue(
                            code=ConfigErrorCode.CROSS_WORKSPACE_METRIC.value,
                            field_path=f"metrics[{idx}].metric_id",
                            message=f"Metric '{mid}' belongs to workspace '{other_ws}', not '{config.workspace}'.",
                            suggested_correction=f"Cross-workspace metric bindings are forbidden. Move the metric or configure workspace: '{other_ws}'."
                        ))
                    else:
                        issues.append(ValidationIssue(
                            code=ConfigErrorCode.METRIC_NOT_FOUND.value,
                            field_path=f"metrics[{idx}].metric_id",
                            message=f"Metric '{mid}' does not exist in MetricGuard Store.",
                            suggested_correction="Verify the Metric UUID from MetricGuard Studio."
                        ))

        # C. Verify snapshot binding (if snapshot_id given)
        if config.snapshot and config.snapshot.snapshot_id:
            sid = config.snapshot.snapshot_id
            with store.engine.connect() as conn:
                from services.api.store import snapshots as snapshots_tbl
                s_row = conn.execute(
                    select(snapshots_tbl).where(
                        snapshots_tbl.c.id == sid,
                        snapshots_tbl.c.project_id == config.workspace
                    )
                ).mappings().first()
                if not s_row:
                    issues.append(ValidationIssue(
                        code=ConfigErrorCode.SNAPSHOT_NOT_FOUND.value,
                        field_path="snapshot.snapshot_id",
                        message=f"Snapshot '{sid}' not found in workspace '{config.workspace}'.",
                        suggested_correction="Confirm the snapshot ID has been ingested into this workspace."
                    ))

    except Exception as exc:
        if not store_connected:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.STORE_UNAVAILABLE.value,
                field_path="",
                message=f"Authoritative Store is unavailable at '{db_url}': {exc}. Run with --schema-only for offline structural validation."
            ))
        else:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.STORE_UNAVAILABLE.value,
                field_path="",
                message=f"Store query error: {exc}"
            ))

    return ValidationResult(
        is_valid=len(issues) == 0,
        mode="governed",
        loaded_config=loaded,
        issues=issues,
        metrics_verified=metrics_verified_count,
        dbt_resources_verified=dbt_verified_count,
        store_connected=store_connected,
    )
