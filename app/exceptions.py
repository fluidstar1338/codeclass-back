import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.services.auth_service import AuthError

logger = logging.getLogger(__name__)


async def auth_error_handler(_request: object, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AuthError)
    logger.exception("AuthError capturado")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


async def integrity_error_handler(_request: object, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IntegrityError)
    logger.exception("IntegrityError capturado")
    return JSONResponse(
        status_code=409,
        content={"detail": "Operação viola uma restrição de integridade dos dados."},
    )


async def generic_error_handler(_request: object, exc: Exception) -> JSONResponse:
    logger.exception("Erro interno no servidor")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno no servidor."},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AuthError, auth_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
