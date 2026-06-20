from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    student_id: str = Field(default="S001")
    question: str
    grade: Optional[str] = None
    vision_status: Optional[str] = None
    subject: Literal["geometry", "english", "general"] = "general"
    language: Literal["en", "vi"] = "en"


class AskResponse(BaseModel):
    answer: str
    subject: str
    suggestions: List[str]
    context_used: List[str] = []


class StudyPlanRequest(BaseModel):
    student_id: str = "S001"
    grade: str = "Grade 8"
    weakness: str = "geometry"
    available_time: str = "25 minutes per day"
    language: Literal["en", "vi"] = "en"


class StudyPlanResponse(BaseModel):
    weekly_plan: List[str]


class ProfilePayload(BaseModel):
    student_id: str = "S001"
    name: str = "Student A"
    grade: str = "Grade 8"
    vision_status: str = "low vision"
    math_level: str = "weak in geometry"
    english_level: str = "A2"
    weaknesses: List[str] = ["geometry", "charts", "self-study"]
    strengths: List[str] = ["listening", "verbal explanation"]
    learning_goal: str = "understand classroom lessons better"


class CommandRequest(BaseModel):
    student_id: str = "S001"
    message: str
    language: Literal["en", "vi"] = "en"


class TTSRequest(BaseModel):
    text: str
    language: Literal["en", "vi"] = "en"
    voice: Optional[str] = None

