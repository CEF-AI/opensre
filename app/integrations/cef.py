"""CEF integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import CefIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = CefIntegrationConfig.model_validate(
            {
                "vault_base_url": credentials.get("vault_base_url", ""),
                "vault_id": credentials.get("vault_id", ""),
                "agent_id": credentials.get("agent_id", ""),
                "wallet_path": credentials.get("wallet_path", ""),
                "wallet_json": credentials.get("wallet_json", ""),
                "wallet_password": credentials.get("wallet_password", ""),
                "cluster": credentials.get("cluster", "") or "dragon1-testnet",
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="cef", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg.model_dump(), "cef"
    return None, None
