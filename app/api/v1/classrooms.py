from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_member, get_db
from app.models.organization import OrganizationMember
from app.schemas.classroom import (
    ClassroomCreate,
    ClassroomEnrollRequest,
    ClassroomResponse,
    ClassroomStudentResponse,
    ClassroomUpdate,
)
from app.services import classroom_service

router = APIRouter()


@router.post(
    "",
    response_model=ClassroomResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma nova turma na organização",
)
async def create_classroom(
    request: ClassroomCreate,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassroomResponse:
    classroom = await classroom_service.create_classroom(
        data=request.model_dump(),
        member=current_member,
        db=db,
    )
    return ClassroomResponse.model_validate(classroom)


@router.get(
    "",
    response_model=list[ClassroomResponse],
    summary="Lista turmas visíveis ao usuário autenticado",
)
async def list_classrooms(
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ClassroomResponse]:
    classrooms = await classroom_service.list_classrooms(
        member=current_member,
        db=db,
    )
    return [ClassroomResponse.model_validate(c) for c in classrooms]


@router.get(
    "/{id}",
    response_model=ClassroomResponse,
    summary="Consulta dados de uma turma específica",
)
async def get_classroom(
    id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassroomResponse:
    classroom = await classroom_service.get_classroom(
        classroom_id=id,
        member=current_member,
        db=db,
    )
    return ClassroomResponse.model_validate(classroom)


@router.patch(
    "/{id}",
    response_model=ClassroomResponse,
    summary="Atualiza dados de uma turma",
)
async def update_classroom(
    id: UUID,
    request: ClassroomUpdate,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassroomResponse:
    classroom = await classroom_service.update_classroom(
        classroom_id=id,
        data=request.model_dump(exclude_unset=True),
        member=current_member,
        db=db,
    )
    return ClassroomResponse.model_validate(classroom)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove uma turma",
)
async def delete_classroom(
    id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await classroom_service.delete_classroom(
        classroom_id=id,
        member=current_member,
        db=db,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Students ──────────────────────────────────────────────────────────


@router.post(
    "/{id}/students",
    response_model=ClassroomStudentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Matricula um aluno na turma",
)
async def enroll_student(
    id: UUID,
    request: ClassroomEnrollRequest,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClassroomStudentResponse:
    enrollment = await classroom_service.enroll_student(
        classroom_id=id,
        student_id=request.student_id,
        member=current_member,
        db=db,
    )
    return ClassroomStudentResponse.model_validate(enrollment)


@router.get(
    "/{id}/students",
    response_model=list[ClassroomStudentResponse],
    summary="Lista alunos matriculados na turma",
)
async def list_students(
    id: UUID,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ClassroomStudentResponse]:
    students = await classroom_service.list_students(
        classroom_id=id,
        member=current_member,
        db=db,
    )
    return [ClassroomStudentResponse.model_validate(s) for s in students]


@router.delete(
    "/{id}/students",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a matrícula de um aluno da turma",
)
async def remove_student(
    id: UUID,
    request: ClassroomEnrollRequest,
    current_member: Annotated[OrganizationMember, Depends(get_current_active_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await classroom_service.remove_student(
        classroom_id=id,
        student_id=request.student_id,
        member=current_member,
        db=db,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
