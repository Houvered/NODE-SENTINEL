# -*- coding: utf-8 -*-
"""Pydantic contracts for the case-scoped evidence platform."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    fir_no: str = Field("", max_length=100)
    police_station: str = Field("", max_length=200)


class CaseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[str] = None
    fir_no: Optional[str] = None
    police_station: Optional[str] = None


class CaseMemberAdd(BaseModel):
    username: str = Field(..., min_length=1)
    case_role: str = Field("investigator")


class CaseResponse(BaseModel):
    id: str
    title: str
    description: str = ""
    status: str
    fir_no: str = ""
    police_station: str = ""
    created_by: str
    created_at: str
    updated_at: str
    archived_at: Optional[str] = None
    retention_until: Optional[str] = None
    my_role: Optional[str] = None


class MemberResponse(BaseModel):
    case_id: str
    user_id: str
    username: str = ""
    case_role: str
    added_by: Optional[str] = None
    added_at: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=200)
    full_name: Optional[str] = None
