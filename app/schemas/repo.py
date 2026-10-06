"""Pydantic v2 schemas for Repository inventory and maintainer onboarding."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class RepoBase(BaseModel):
    owner: str
    name: str
    description: str | None = None
    stars: int = 0
    primary_language: str | None = None


class RepoCreate(RepoBase):
    github_id: int | None = None


class RepoUpdate(BaseModel):
    description: str | None = None
    stars: int | None = None
    primary_language: str | None = None
    claimed: bool | None = None
    claimed_by: str | None = None
    maintainer_handle: str | None = None
    payout_address: str | None = None


class RepoResponse(RepoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_id: int | None = None
    claimed: bool
    claimed_by: str | None = None
    maintainer_handle: str | None = None
    claimed_at: datetime | None = None
    payout_address: str | None = None
    created_at: datetime
    updated_at: datetime


class ClaimRepoRequest(BaseModel):
    owner: str
    name: str | None = None
    repo: str | None = None
    maintainer_handle: str
    payout_address: str | None = None

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("owner cannot be empty or whitespace.")
        return v.strip()

    @field_validator("maintainer_handle")
    @classmethod
    def validate_maintainer_handle(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("maintainer_handle cannot be empty or whitespace.")
        return v.strip()

    @field_validator("payout_address")
    @classmethod
    def validate_payout_address(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            return stripped if stripped else None
        return None

    @model_validator(mode="after")
    def populate_name(self) -> "ClaimRepoRequest":
        clean_name = self.name.strip() if self.name else None
        clean_repo = self.repo.strip() if self.repo else None
        if not clean_name and not clean_repo:
            raise ValueError("Either 'name' or 'repo' must be provided.")
        self.name = clean_name or clean_repo
        self.repo = clean_repo or clean_name
        return self


# Alias for ClaimRequest
ClaimRequest = ClaimRepoRequest


class ClaimRepoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str = "Repository claimed successfully."
    # Top-level repository attributes for direct field access
    id: int | None = None
    owner: str | None = None
    name: str | None = None
    description: str | None = None
    stars: int | None = 0
    primary_language: str | None = None
    ci_status: str | None = None
    claimed: bool = True
    claimed_by: str | None = None
    maintainer_handle: str | None = None
    claimed_at: datetime | None = None
    payout_address: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Nested repository object & snippet map
    repository: RepoResponse | None = None
    snippets: dict[str, str] | None = None


# Alias for ClaimResponse
ClaimResponse = ClaimRepoResponse


class SnippetResponse(BaseModel):
    owner: str
    name: str
    markdown: str
    html: str
    rst: str
    badge_url: str | None = None
    click_url: str | None = None
