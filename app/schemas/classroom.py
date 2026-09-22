from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClassroomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    teacher_id: UUID


class ClassroomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None


class ClassroomResponse(BaseModel):
    id: UUID
    organization_id: UUID
    teacher_id: UUID
    name: str
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClassroomEnrollRequest(BaseModel):
    student_id: UUID


class ClassroomStudentResponse(BaseModel):
    classroom_id: UUID
    student_id: UUID
    enrolled_at: datetime

    model_config = ConfigDict(from_attributes=True)
