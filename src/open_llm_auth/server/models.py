from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Union[str, List[Dict[str, Any]], List[Any], Dict[str, Any]]
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: List[Message]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = Field(default=None, alias="max_tokens")
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: Union[str, List[str]]
    dimensions: Optional[int] = None
    encoding_format: Optional[str] = Field(default=None, alias="encoding_format")
    user: Optional[str] = None


class UniversalMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Union[str, List[Dict[str, Any]], List[Any], Dict[str, Any]]
    name: Optional[str] = None


class UniversalRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: List[UniversalMessage] = Field(default_factory=list)
    task: Optional[Dict[str, Any]] = None
    options: Dict[str, Any] = Field(default_factory=dict)
    stream: bool = False
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class UniversalTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openbulma"
    task: Dict[str, Any]
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class UniversalTaskRetryRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openbulma"
    operator: Optional[str] = None
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class UniversalTaskApproveRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openbulma"
    approval_id: str = Field(alias="approvalId")
    approved: bool = True
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class UniversalTaskCancelRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openbulma"
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class UniversalTaskListResponse(BaseModel):
    object: str = "universal.task.list"
    provider: str
    model: str
    profile: Optional[str] = None
    auth_source: str
    tasks: List[Dict[str, Any]]


class UniversalTaskEventListResponse(BaseModel):
    object: str = "universal.task.events"
    provider: str
    model: str
    profile: Optional[str] = None
    auth_source: str
    task_id: str
    events: List[Dict[str, Any]]


class UniversalTaskWaitRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openbulma"
    timeout_ms: int = Field(default=30000, alias="timeoutMs")
    poll_ms: int = Field(default=1000, alias="pollMs")
    auth_profile: Optional[str] = Field(default=None, alias="authProfile")


class ModelList(BaseModel):
    object: str = "list"
    data: List[Dict[str, Any]]
