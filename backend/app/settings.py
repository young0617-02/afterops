from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    allowed_origins: str = (
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:8000,http://localhost:8000"
    )
    demo_merchant_name: str = "AfterOps 演示 3C 店"
    database_url: str = "postgresql+psycopg://afterops:afterops@127.0.0.1:5433/afterops"
    session_secret: str = "dev-only-change-me"
    mock_psp_secret: str = "dev-only-change-me"
    demo_owner_password: str = "owner123456"
    demo_agent_password: str = "agent123456"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_request_limit: int = 5
    otp_request_window_seconds: int = 600
    login_limit: int = 10
    login_window_seconds: int = 600
    admin_login_limit: int = 5
    admin_login_window_seconds: int = 600
    agent_online_window_seconds: int = 90
    chat_limit: int = 30
    chat_window_seconds: int = 60
    checkout_limit: int = 10
    checkout_window_seconds: int = 60
    log_format: str = "text"
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "afterops-api"
    llm_provider: str = "template"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    llm_timeout_ms: int = 20000
    compensation_max_cents: int = 50000
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_from: str = "afterops@local"
    admin_base_url: str = "http://127.0.0.1:3000"
    email_notify_enabled: bool = True
    customer_email_notify_enabled: bool = True
    outbox_poll_seconds: float = 5.0
    outbox_max_attempts: int = 5
    return_address: str = "上海市青浦区华新镇华志路 168 号 AfterOps 退货仓"
    upload_dir: str = "uploads"
    upload_max_bytes: int = 5 * 1024 * 1024
    order_auto_complete_days: int = 7
    order_auto_complete_interval_seconds: int = 3600
    order_expire_interval_seconds: int = 60
    low_stock_threshold: int = 5
    proposal_sla_minutes: int = 30
    handoff_resolution_sla_hours: int = 24
    handoff_sla_minutes: int = 15
    handoff_sla_high_minutes: int = 10
    handoff_sla_urgent_minutes: int = 5
    handoff_first_response_minutes: int = 30
    handoff_first_response_escalation_minutes: int = 60
    sla_reminder_interval_seconds: int = 300
    after_sales_reminder_interval_seconds: int = 300
    after_sales_quote_reminder_hours: int = 24
    after_sales_settlement_reminder_hours: int = 24
    after_sales_appeal_reminder_hours: int = 24
    after_sales_supplement_reminder_hours: int = 24
    after_sales_supplement_timeout_hours: int = 72
    refund_auto_settle_minutes: int = 5
    refund_auto_retry_minutes: int = 15
    refund_auto_retry_max_attempts: int = 3
    audit_anchor_dir: str = "anchors"
    audit_anchor_secret: str = ""
    audit_anchor_interval_seconds: int = 86400

    @model_validator(mode="after")
    def _production_secrets(self):
        if self.app_env != "prod":
            return self
        problems = []
        if self.session_secret in ("", "dev-only-change-me") or len(
            self.session_secret
        ) < 32:
            problems.append("SESSION_SECRET 必须替换为至少 32 字符")
        if self.mock_psp_secret in ("", "dev-only-change-me") or len(
            self.mock_psp_secret
        ) < 16:
            problems.append("MOCK_PSP_SECRET 必须替换为至少 16 字符")
        if self.demo_owner_password == "owner123456":
            problems.append("DEMO_OWNER_PASSWORD 不能使用默认值")
        if self.demo_agent_password == "agent123456":
            problems.append("DEMO_AGENT_PASSWORD 不能使用默认值")
        if len(self.audit_anchor_secret) < 32:
            problems.append("AUDIT_ANCHOR_SECRET 必须至少 32 字符")
        if problems:
            raise ValueError("生产环境配置不安全：" + "；".join(problems))
        return self

    @property
    def allowed_origin_set(self) -> set[str]:
        return {
            item.strip().rstrip("/")
            for item in self.allowed_origins.split(",")
            if item.strip()
        }


settings = Settings()
