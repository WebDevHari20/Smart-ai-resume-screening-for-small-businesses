from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    company: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str = Field(min_length=20)
    required_skills: list[str] | None = None  # auto-extracted from the description when omitted
    min_experience: float = Field(0, ge=0, le=40)
    education_level: Literal["none", "diploma", "bachelor", "master", "phd"] = "none"
    requires_proof_of_work: bool = True


class WeightsIn(BaseModel):
    skills: float = Field(ge=0)
    experience: float = Field(ge=0)
    education: float = Field(ge=0)
    semantic: float = Field(ge=0)
    proof_of_work: float = Field(ge=0)

    @field_validator("proof_of_work")
    @classmethod
    def _nonzero_total(cls, v, info):
        if v + sum(info.data.get(k, 0) for k in ("skills", "experience", "education", "semantic")) <= 0:
            raise ValueError("weights must not all be zero")
        return v


class FeedbackIn(BaseModel):
    action: Literal["shortlist", "reject", "interview", "note"]
    note: str = ""
