from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError

from app.auth.jwt_auth import JWTVerificationError, verify_jwt_async
from app.config import LLMSettings, get_environment
from app.services.cef.qa import CefQaRequest, CefQaResult, run_cef_qa
from app.utils.sentry_sdk import init_sentry
from app.version import get_version

init_sentry(entrypoint="webapp")


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str


app = FastAPI()

_bearer = HTTPBearer(auto_error=True)


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    """Gate the microservice behind a verified JWT (Clerk JWKS).

    Sergei's constraint: a hosted investigate API must not let anyone fire investigations. The
    health endpoints stay open; every investigate call needs a valid bearer token.
    """
    try:
        await verify_jwt_async(credentials.credentials)
    except JWTVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def _llm_configured() -> bool:
    try:
        LLMSettings.from_env()
    except ValidationError:
        return False
    return True


def get_health_response() -> HealthResponse:
    llm_configured = _llm_configured()

    return HealthResponse(
        ok=llm_configured,
        version=get_version(),
        llm_configured=llm_configured,
        env=get_environment().value,
    )


@app.get("/", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse)
@app.get("/ok", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    health_response = get_health_response()
    response.status_code = (
        status.HTTP_200_OK if health_response.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return health_response


@app.post("/investigate", response_model=CefQaResult, dependencies=[Depends(require_auth)])
def investigate(request: CefQaRequest) -> CefQaResult:
    """Run one CEF hiring-coach QA investigation for the caller's own vault.

    Multi-tenant door onto the same core as the CLI: creds (vault + wallet, optional Grafana) come
    in the request, so the server holds no wallet. Returns the verdict; also posts the beautified
    report to Telegram when ``deliver_telegram`` is supplied. Runs in FastAPI's threadpool (sync).
    """
    return run_cef_qa(request)
