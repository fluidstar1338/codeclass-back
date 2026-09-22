import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.classroom import Classroom, ClassroomStudent
from app.models.enums import OrgRole
from app.models.organization import OrganizationMember

logger = logging.getLogger(__name__)


async def create_classroom(
    data: dict,
    member: OrganizationMember,
    db: AsyncSession,
) -> Classroom:
    """Cria uma turma na organização do membro autenticado."""
    if member.role not in (OrgRole.OWNER, OrgRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas gestores (Owner/Admin) podem criar turmas.",
        )

    # Verifica se o teacher_id pertence à organização
    teacher_member = await _get_org_member(data["teacher_id"], member.organization_id, db)
    if teacher_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Professor não encontrado nesta organização.",
        )
    if teacher_member.role not in (OrgRole.TEACHER, OrgRole.OWNER, OrgRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O usuário indicado não possui papel de professor.",
        )

    classroom = Classroom(
        organization_id=member.organization_id,
        teacher_id=data["teacher_id"],
        name=data["name"],
        description=data.get("description"),
    )
    db.add(classroom)
    try:
        await db.commit()
        await db.refresh(classroom)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("Duplicata ao criar turma")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operação viola uma restrição de integridade dos dados.",
        ) from e
    return classroom


async def list_classrooms(
    member: OrganizationMember,
    db: AsyncSession,
) -> list[Classroom]:
    """Lista turmas visíveis ao membro conforme RBAC."""
    org_id = member.organization_id

    if member.role in (OrgRole.OWNER, OrgRole.ADMIN):
        stmt = (
            select(Classroom)
            .where(Classroom.organization_id == org_id)
            .order_by(Classroom.created_at.desc())
        )
    elif member.role == OrgRole.TEACHER:
        stmt = (
            select(Classroom)
            .where(
                Classroom.organization_id == org_id,
                Classroom.teacher_id == member.user_id,
            )
            .order_by(Classroom.created_at.desc())
        )
    else:
        # STUDENT: só vê turmas em que está matriculado
        stmt = (
            select(Classroom)
            .join(ClassroomStudent, ClassroomStudent.classroom_id == Classroom.id)
            .where(
                Classroom.organization_id == org_id,
                ClassroomStudent.student_id == member.user_id,
            )
            .order_by(Classroom.created_at.desc())
        )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_classroom(
    classroom_id: UUID,
    member: OrganizationMember,
    db: AsyncSession,
) -> Classroom:
    """Obtém uma turma com verificação de tenant e RBAC."""
    classroom = await _fetch_classroom_or_404(classroom_id, member.organization_id, db)

    # RBAC: estudante só vê se estiver matriculado
    if member.role == OrgRole.STUDENT:
        enrolled = await db.execute(
            select(ClassroomStudent).where(
                ClassroomStudent.classroom_id == classroom_id,
                ClassroomStudent.student_id == member.user_id,
            )
        )
        if enrolled.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Turma não encontrada.",
            )

    # TEACHER: só vê se for o responsável
    if member.role == OrgRole.TEACHER and classroom.teacher_id != member.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Turma não encontrada.",
        )

    return classroom


async def update_classroom(
    classroom_id: UUID,
    data: dict,
    member: OrganizationMember,
    db: AsyncSession,
) -> Classroom:
    """Atualiza uma turma (Owner/Admin ou professor responsável)."""
    classroom = await _fetch_classroom_or_404(classroom_id, member.organization_id, db)
    _require_manager_or_responsible(member, classroom)

    if data.get("name") is not None:
        classroom.name = data["name"]
    if "description" in data:
        classroom.description = data["description"]

    try:
        await db.commit()
        await db.refresh(classroom)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("Erro de integridade ao atualizar turma")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operação viola uma restrição de integridade dos dados.",
        ) from e
    return classroom


async def delete_classroom(
    classroom_id: UUID,
    member: OrganizationMember,
    db: AsyncSession,
) -> None:
    """Remove uma turma (Owner/Admin ou professor responsável)."""
    classroom = await _fetch_classroom_or_404(classroom_id, member.organization_id, db)
    _require_manager_or_responsible(member, classroom)

    await db.delete(classroom)
    await db.commit()


# ── Students (matrícula) ──────────────────────────────────────────────


async def enroll_student(
    classroom_id: UUID,
    student_id: UUID,
    member: OrganizationMember,
    db: AsyncSession,
) -> ClassroomStudent:
    """Matricula um aluno na turma."""
    classroom = await _fetch_classroom_or_404(classroom_id, member.organization_id, db)
    _require_manager_or_responsible(member, classroom)

    # Verifica se o aluno pertence à organização
    student_member = await _get_org_member(student_id, member.organization_id, db)
    if student_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado nesta organização.",
        )

    enrollment = ClassroomStudent(
        classroom_id=classroom_id,
        student_id=student_id,
    )
    db.add(enrollment)
    try:
        await db.commit()
        await db.refresh(enrollment)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("Duplicata ao matricular aluno")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aluno já matriculado nesta turma.",
        ) from e
    return enrollment


async def list_students(
    classroom_id: UUID,
    member: OrganizationMember,
    db: AsyncSession,
) -> list[ClassroomStudent]:
    """Lista alunos matriculados na turma."""
    await _fetch_classroom_or_404(classroom_id, member.organization_id, db)

    stmt = (
        select(ClassroomStudent)
        .where(ClassroomStudent.classroom_id == classroom_id)
        .order_by(ClassroomStudent.enrolled_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def remove_student(
    classroom_id: UUID,
    student_id: UUID,
    member: OrganizationMember,
    db: AsyncSession,
) -> None:
    """Remove a matrícula de um aluno da turma."""
    classroom = await _fetch_classroom_or_404(classroom_id, member.organization_id, db)
    _require_manager_or_responsible(member, classroom)

    stmt = select(ClassroomStudent).where(
        ClassroomStudent.classroom_id == classroom_id,
        ClassroomStudent.student_id == student_id,
    )
    result = await db.execute(stmt)
    enrollment = result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Matrícula não encontrada.",
        )

    await db.delete(enrollment)
    await db.commit()


# ── Helpers privados ──────────────────────────────────────────────────


async def _fetch_classroom_or_404(
    classroom_id: UUID,
    organization_id: UUID,
    db: AsyncSession,
) -> Classroom:
    """Busca turma e garante que pertence à organização (cross-tenant → 404)."""
    result = await db.execute(
        select(Classroom).where(Classroom.id == classroom_id)
    )
    classroom = result.scalar_one_or_none()
    if classroom is None or classroom.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Turma não encontrada.",
        )
    return classroom


async def _get_org_member(
    user_id: UUID,
    organization_id: UUID,
    db: AsyncSession,
) -> OrganizationMember | None:
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


def _require_manager_or_responsible(
    member: OrganizationMember,
    classroom: Classroom,
) -> None:
    """Owner/Admin = manager. TEACHER só se for o responsável."""
    if member.role in (OrgRole.OWNER, OrgRole.ADMIN):
        return
    if member.role == OrgRole.TEACHER and classroom.teacher_id == member.user_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso negado: permissão insuficiente para executar esta ação.",
    )
