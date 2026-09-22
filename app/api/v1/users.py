import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_active_member, get_current_user, get_db
from app.models.organization import OrganizationMember
from app.models.user import User
from app.schemas.organization import OrganizationMemberResponse
from app.schemas.user import (
    CurrentUserProfileResponse,
    UserPasswordChangeRequest,
    UserProfileUpdateRequest,
)
from app.services.auth_service import AuthError, auth_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.patch(
    "/me",
    response_model=CurrentUserProfileResponse,
    summary="Atualiza dados cadastrais do próprio perfil",
)
async def update_my_profile(
    request: UserProfileUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUserProfileResponse:
    user_id = current_user.id
    old_full_name = current_user.full_name

    try:
        await auth_service.update_auth_user(
            user_id=user_id,
            full_name=request.full_name,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    current_user.full_name = request.full_name
    try:
        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)
    except Exception as e:
        await db.rollback()
        # Rollback compensatório no Supabase Auth para restaurar o nome anterior
        try:
            await auth_service.update_auth_user(
                user_id=user_id,
                full_name=old_full_name,
            )
        except AuthError as cleanup_err:
            logger.warning(
                "Falha ao reverter metadados no auth provider no rollback de usuário %s: %s",
                user_id,
                cleanup_err,
            )
        logger.exception("Erro ao atualizar usuário no banco de dados")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao atualizar usuário no banco de dados.",
        ) from e

    return CurrentUserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        organization_id=current_member.organization_id,
        organization_name=current_member.organization.name,
        organization_slug=current_member.organization.slug,
        role=current_member.role,
        is_active=current_member.is_active,
        created_at=current_user.created_at,
    )


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Altera a senha do usuário autenticado",
)
async def update_my_password(
    request: UserPasswordChangeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    _current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
) -> Response:
    try:
        await auth_service.update_auth_user(
            user_id=current_user.id,
            password=request.password,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{user_id}",
    response_model=OrganizationMemberResponse,
    summary="Consulta dados de um usuário da mesma organização",
    description=(
        "Retorna os dados cadastrais e papel do usuário especificado, restrito aos membros "
        "da mesma organização do solicitante (RNF01). Membros desativados permanecem consultáveis "
        "para fins de histórico e auditoria da instituição."
    ),
)
async def get_user_by_id(
    user_id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationMemberResponse:
    stmt = (
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == current_member.organization_id,
        )
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()

    if member is None or member.user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado na organização.",
        )

    return OrganizationMemberResponse(
        organization_id=member.organization_id,
        user_id=member.user.id,
        email=member.user.email,
        full_name=member.user.full_name,
        role=member.role,
        is_active=member.is_active,
        joined_at=member.joined_at,
    )
