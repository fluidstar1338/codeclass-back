import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    get_current_active_member,
    get_db,
    require_admin_or_owner,
    require_owner,
    verify_org_access,
)
from app.models.classroom import Classroom
from app.models.enums import OrgRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.schemas.organization import (
    ClassroomSummaryResponse,
    MemberRoleUpdateRequest,
    MemberStatusUpdateRequest,
    OrganizationMemberCreate,
    OrganizationMemberResponse,
    OrganizationRegisterRequest,
    OrganizationResponse,
    OrganizationUpdateRequest,
    TransferOwnershipRequest,
)
from app.services.auth_service import AuthError, auth_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastro público da organização com responsável inicial (Owner)",
)
async def register_organization(
    request: OrganizationRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    # Verifica duplicidade de slug
    slug_res = await db.execute(
        select(Organization).where(Organization.slug == request.slug)
    )
    if slug_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma organização com este slug.",
        )

    # Verifica duplicidade de email
    email_res = await db.execute(
        select(User).where(User.email == request.owner.email)
    )
    if email_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário cadastrado com este e-mail.",
        )

    # 1. Cria usuário no provedor de autenticação
    try:
        auth_user = await auth_service.create_auth_user(
            email=request.owner.email,
            password=request.owner.password,
            full_name=request.owner.full_name,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    user_id = auth_user["id"]

    try:
        # 2. Persiste usuário local
        owner_user = User(
            id=user_id,
            email=request.owner.email,
            full_name=request.owner.full_name,
        )
        db.add(owner_user)
        await db.flush()

        # 3. Cria organização
        organization = Organization(
            name=request.name,
            slug=request.slug,
            owner_id=owner_user.id,
        )
        db.add(organization)
        await db.flush()

        # 4. Cria vínculo como Owner
        member = OrganizationMember(
            organization_id=organization.id,
            user_id=owner_user.id,
            role=OrgRole.OWNER,
            is_active=True,
        )
        db.add(member)

        await db.commit()
        await db.refresh(organization)
        return OrganizationResponse.model_validate(organization)
    except IntegrityError as e:
        await db.rollback()
        try:
            await auth_service.delete_auth_user(user_id)
        except AuthError as cleanup_err:
            logger.warning("Falha ao remover usuário do provedor no rollback: %s", cleanup_err)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organização com este slug ou usuário com este e-mail já existe.",
        ) from e
    except Exception as e:
        await db.rollback()
        # Rollback do usuário no provedor de autenticação em caso de falha no banco
        try:
            await auth_service.delete_auth_user(user_id)
        except AuthError as cleanup_err:
            logger.warning("Falha ao remover usuário do provedor no rollback: %s", cleanup_err)
        logger.exception("Erro ao cadastrar organização")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao cadastrar organização.",
        ) from e


@router.get(
    "/{org_id}",
    response_model=OrganizationResponse,
    summary="Consulta dados cadastrais da organização",
    dependencies=[Depends(verify_org_access)],
)
async def get_organization(
    org_id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    res = await db.execute(select(Organization).where(Organization.id == org_id))
    org = res.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organização não encontrada.",
        )
    return OrganizationResponse.model_validate(org)


@router.patch(
    "/{org_id}",
    response_model=OrganizationResponse,
    summary="Atualiza dados cadastrais da organização (Apenas Owner)",
    dependencies=[Depends(verify_org_access)],
)
async def update_organization(
    org_id: UUID,
    request: OrganizationUpdateRequest,
    current_member: Annotated[OrganizationMember, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    res = await db.execute(select(Organization).where(Organization.id == org_id))
    org = res.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organização não encontrada.",
        )

    if request.slug is not None and request.slug != org.slug:
        slug_check = await db.execute(
            select(Organization).where(
                Organization.slug == request.slug, Organization.id != org_id
            )
        )
        if slug_check.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este slug já está em uso por outra organização.",
            )
        org.slug = request.slug

    if request.name is not None:
        org.name = request.name

    try:
        await db.commit()
        await db.refresh(org)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este slug já está em uso por outra organização.",
        ) from e
    return OrganizationResponse.model_validate(org)


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui definitivamente a organização (Apenas Owner)",
    dependencies=[Depends(verify_org_access)],
)
async def delete_organization(
    org_id: UUID,
    current_member: Annotated[OrganizationMember, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    res = await db.execute(select(Organization).where(Organization.id == org_id))
    org = res.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organização não encontrada.",
        )

    # 1. Coleta os user_id de todos os membros da organização (incluindo o owner)
    members_res = await db.execute(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == org_id
        )
    )
    user_ids = members_res.scalars().all()

    # 2. Deleta a organização (cascateia para classrooms, assignments, submissions, messages e organization_members)
    await db.delete(org)
    await db.flush()

    # 3. Deleta os usuários da organização (o trigger no PostgreSQL remove de auth.users automaticamente)
    if user_ids:
        await db.execute(delete(User).where(User.id.in_(user_ids)))

    await db.commit()

    # 4. Fallback defensivo para garantir limpeza no provedor de autenticação
    if user_ids:
        async def _safe_delete(uid: UUID) -> None:
            try:
                await auth_service.delete_auth_user(uid)
            except AuthError:
                pass  # Já removido pelo trigger de banco

        await asyncio.gather(*[_safe_delete(uid) for uid in user_ids], return_exceptions=True)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{org_id}/members",
    response_model=OrganizationMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra novo membro institucional na organização",
    dependencies=[Depends(verify_org_access)],
)
async def add_member(
    org_id: UUID,
    request: OrganizationMemberCreate,
    current_member: Annotated[OrganizationMember, Depends(require_admin_or_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationMemberResponse:
    # Não permite criar outro Owner diretamente
    if request.role == OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é permitido criar um membro diretamente com papel de proprietário.",
        )

    # Regra RBAC: Apenas o Owner pode cadastrar Administradores (HLD matriz RBAC)
    if request.role == OrgRole.ADMIN and current_member.role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o proprietário (Owner) pode cadastrar novos administradores.",
        )

    # Verifica se e-mail já existe
    email_res = await db.execute(select(User).where(User.email == request.email))
    if email_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário com este e-mail cadastrado.",
        )

    # 1. Cria usuário no provedor de autenticação
    try:
        auth_user = await auth_service.create_auth_user(
            email=request.email,
            password=request.password,
            full_name=request.full_name,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    user_id = auth_user["id"]

    try:
        # 2. Persiste usuário no banco local
        new_user = User(
            id=user_id,
            email=request.email,
            full_name=request.full_name,
        )
        db.add(new_user)
        await db.flush()

        # 3. Cria vínculo na organização
        new_member = OrganizationMember(
            organization_id=org_id,
            user_id=new_user.id,
            role=request.role,
            is_active=True,
        )
        db.add(new_member)
        await db.commit()
        await db.refresh(new_member)

        return OrganizationMemberResponse(
            organization_id=new_member.organization_id,
            user_id=new_user.id,
            email=new_user.email,
            full_name=new_user.full_name,
            role=new_member.role,
            is_active=new_member.is_active,
            joined_at=new_member.joined_at,
        )
    except IntegrityError as e:
        await db.rollback()
        try:
            await auth_service.delete_auth_user(user_id)
        except AuthError as cleanup_err:
            logger.warning("Falha ao remover usuário do provedor no rollback de membro: %s", cleanup_err)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Membro com este e-mail já cadastrado.",
        ) from e
    except Exception as e:
        await db.rollback()
        try:
            await auth_service.delete_auth_user(user_id)
        except AuthError as cleanup_err:
            logger.warning("Falha ao remover usuário do provedor no rollback de membro: %s", cleanup_err)
        logger.exception("Erro ao cadastrar membro")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao cadastrar membro.",
        ) from e


@router.get(
    "/{org_id}/members",
    response_model=list[OrganizationMemberResponse],
    summary="Lista todos os membros vinculados à instituição",
    dependencies=[Depends(verify_org_access)],
)
async def list_members(
    org_id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
    role: OrgRole | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[OrganizationMemberResponse]:
    stmt = (
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(OrganizationMember.organization_id == org_id)
    )

    if role is not None:
        stmt = stmt.where(OrganizationMember.role == role)
    if is_active is not None:
        stmt = stmt.where(OrganizationMember.is_active == is_active)
    if search:
        search_filter = f"%{search.lower()}%"
        stmt = stmt.join(OrganizationMember.user).where(
            or_(
                func.lower(User.full_name).like(search_filter),
                func.lower(User.email).like(search_filter),
            )
        )

    stmt = stmt.order_by(OrganizationMember.joined_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    members = result.scalars().all()

    return [
        OrganizationMemberResponse(
            organization_id=m.organization_id,
            user_id=m.user.id,
            email=m.user.email,
            full_name=m.user.full_name,
            role=m.role,
            is_active=m.is_active,
            joined_at=m.joined_at,
        )
        for m in members
    ]


@router.put(
    "/{org_id}/members/{user_id}/role",
    response_model=OrganizationMemberResponse,
    summary="Atualiza o papel institucional do membro",
    dependencies=[Depends(verify_org_access)],
)
async def update_member_role(
    org_id: UUID,
    user_id: UUID,
    request: MemberRoleUpdateRequest,
    current_member: Annotated[OrganizationMember, Depends(require_admin_or_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationMemberResponse:
    if request.role == OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Para transferir a posse da organização, utilize o endpoint de transferência de titularidade.",
        )

    # RBAC: apenas o Owner pode alterar papéis para/de Admin
    if request.role == OrgRole.ADMIN and current_member.role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o proprietário (Owner) pode promover membros para administrador.",
        )

    stmt = (
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    result = await db.execute(stmt)
    target_member = result.scalar_one_or_none()

    if target_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membro não encontrado nesta organização.",
        )

    if target_member.role == OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O papel do proprietário não pode ser modificado por esta rota.",
        )

    if target_member.role == OrgRole.ADMIN and current_member.role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o proprietário (Owner) pode rebaixar administradores.",
        )

    target_member.role = request.role
    await db.commit()
    await db.refresh(target_member)

    return OrganizationMemberResponse(
        organization_id=target_member.organization_id,
        user_id=target_member.user.id,
        email=target_member.user.email,
        full_name=target_member.user.full_name,
        role=target_member.role,
        is_active=target_member.is_active,
        joined_at=target_member.joined_at,
    )


@router.patch(
    "/{org_id}/members/{user_id}/status",
    response_model=OrganizationMemberResponse,
    summary="Ativa ou desativa o acesso do membro na organização",
    dependencies=[Depends(verify_org_access)],
)
async def update_member_status(
    org_id: UUID,
    user_id: UUID,
    request: MemberStatusUpdateRequest,
    current_member: Annotated[OrganizationMember, Depends(require_admin_or_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationMemberResponse:
    if current_member.user_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é permitido alterar o próprio status de ativação.",
        )

    stmt = (
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    result = await db.execute(stmt)
    target_member = result.scalar_one_or_none()

    if target_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membro não encontrado nesta organização.",
        )

    if target_member.role == OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é permitido desativar o proprietário da organização.",
        )

    # Admin não pode desativar outro admin nem o owner
    if target_member.role == OrgRole.ADMIN and current_member.role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o proprietário pode desativar administradores.",
        )

    target_member.is_active = request.is_active
    await db.commit()
    await db.refresh(target_member)

    return OrganizationMemberResponse(
        organization_id=target_member.organization_id,
        user_id=target_member.user.id,
        email=target_member.user.email,
        full_name=target_member.user.full_name,
        role=target_member.role,
        is_active=target_member.is_active,
        joined_at=target_member.joined_at,
    )


@router.put(
    "/{org_id}/owner",
    response_model=OrganizationResponse,
    summary="Transfere a titularidade da instituição para outro membro (Apenas Owner)",
    dependencies=[Depends(verify_org_access)],
)
async def transfer_ownership(
    org_id: UUID,
    request: TransferOwnershipRequest,
    current_member: Annotated[OrganizationMember, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    if request.new_owner_id == current_member.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O usuário indicado já é o proprietário da organização.",
        )

    # Verifica se o novo proprietário é membro ativo da organização
    stmt = select(OrganizationMember).where(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == request.new_owner_id,
    )
    res = await db.execute(stmt)
    new_owner_member = res.scalar_one_or_none()

    if new_owner_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="O novo proprietário deve ser um membro vinculado a esta organização.",
        )

    if not new_owner_member.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível transferir a posse para um membro desativado.",
        )

    # Busca organização
    org_res = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = org_res.scalar_one_or_none()
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organização não encontrada.",
        )

    # Transfere posse:
    # 1. Atualiza antigo owner para admin (HLD linha 85)
    current_member.role = OrgRole.ADMIN
    # 2. Atualiza novo owner para owner
    new_owner_member.role = OrgRole.OWNER
    # 3. Atualiza owner_id na organização
    organization.owner_id = request.new_owner_id

    await db.commit()
    await db.refresh(organization)

    return OrganizationResponse.model_validate(organization)


@router.get(
    "/{org_id}/classrooms",
    response_model=list[ClassroomSummaryResponse],
    summary="Lista todas as turmas da instituição para auditoria e supervisão (Admin, Owner)",
    dependencies=[Depends(verify_org_access)],
)
async def list_organization_classrooms(
    org_id: UUID,
    current_member: Annotated[OrganizationMember, Depends(require_admin_or_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ClassroomSummaryResponse]:
    stmt = (
        select(Classroom)
        .where(Classroom.organization_id == org_id)
        .order_by(Classroom.created_at.desc())
    )
    result = await db.execute(stmt)
    classrooms = result.scalars().all()
    return [ClassroomSummaryResponse.model_validate(c) for c in classrooms]

