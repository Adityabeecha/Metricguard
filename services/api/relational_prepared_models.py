from typing import Literal
from pydantic import Field
from .models import Input, RelationalStarContractInput


class RelationalPreparedContractInput(RelationalStarContractInput):
    version: int = Field(default=3, strict=True, ge=3, le=3)
    policy: Literal['relational-star-prepared-v1']


class PrepareRelationalInput(Input):
    name: str = Field(min_length=1, max_length=160)
    joins: dict[str, Literal['left', 'inner']]
    selected_columns: list[str] = Field(max_length=8)
