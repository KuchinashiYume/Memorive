from __future__ import annotations

from memorive_app.contracts import error_envelope

from .contracts import ModelConfigurationError


ERROR_CODE_CATALOG = {
    "CREDENTIAL_REF_MISSING": ("credential", False, "请重新选择或保存 API 凭据。"),
    "CREDENTIAL_BACKEND_UNAVAILABLE": ("credential", True, "请检查凭据存储后重试。"),
    "CREDENTIAL_REVOKED": ("credential", False, "请保存新的 API 凭据。"),
    "CREDENTIAL_REPLACE_REQUIRED": ("credential", False, "请使用替换凭据操作。"),
    "CONNECTION_NOT_VERIFIED": ("connection", False, "请先运行连接检查。"),
    "PROVIDER_PRESET_UNKNOWN": ("config", False, "请选择受支持的提供商。"),
    "TIER_POLICY_STALE": ("config", False, "请更新模型档位策略。"),
    "TIER_PRICE_UNKNOWN_NOT_ECONOMY": ("budget", False, "请选择其他档位或更新价格证据。"),
    "TIER_PROFILE_UNAVAILABLE": ("capability", False, "当前配置下该档位不可用。"),
    "TIER_COLLAPSE_NOT_DECLARED": ("config", False, "请使用已披露的档位重合策略。"),
    "NATIVE_EFFORT_UNSUPPORTED": ("capability", False, "请选择该模型支持的思考配置。"),
    "NATIVE_EFFORT_CAPABILITY_STALE": ("capability", False, "请更新模型能力证据。"),
    "MAXIMUM_REQUIRES_EXPLICIT_SELECTION": ("config", False, "请显式选择最高档。"),
    "ROLE_MAPPING_UNRESOLVED": ("config", False, "请修正角色映射。"),
    "QUALIFICATION_NOT_ASSESSED": ("capability", False, "请使用已完成角色资格的配置。"),
    "THINKING_UNSUPPORTED": ("capability", False, "请关闭思考或选择支持思考的配置。"),
    "THINKING_OFF_UNSUPPORTED": ("capability", False, "请选择可关闭思考的替代配置。"),
    "THINKING_NOT_APPLICABLE": ("capability", False, "该配置不使用思考参数。"),
    "DATA_BOUNDARY_BLOCKS_ROUTE": ("privacy", False, "请选择满足数据边界的本地或受控配置。"),
    "FALLBACK_REQUIRES_EXPLICIT_POLICY": ("config", False, "请显式确认回退策略。"),
    "RETURNED_MODEL_IDENTITY_DRIFT": ("connection", False, "请检查提供商与模型身份。"),
    "CONFIG_OVERLAY_CONFLICT": ("config", False, "请解决高级配置冲突。"),
}


def configuration_error_envelope(
    error: ModelConfigurationError,
    *,
    correlation_id: str,
) -> dict:
    category, retryable, user_action = ERROR_CODE_CATALOG.get(
        error.code,
        ("validation", False, "请检查配置后重试。"),
    )
    return error_envelope(
        code=error.code,
        category=category,
        severity="error",
        retryable=retryable,
        user_action=user_action,
        technical_summary=error.code,
        correlation_id=correlation_id,
        support_bundle_recommended=category in {"connection", "capability"},
    )


__all__ = ["ERROR_CODE_CATALOG", "configuration_error_envelope"]
