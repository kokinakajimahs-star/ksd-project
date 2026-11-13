"""数の入力を、保存する列の ``Numeric(精度, 小数の桁)`` に収まる値だけにする（docs/decisions/ASSUMPTIONS.md A-49 の 4。OQ-42 (8)）。

数（float）で受ける入力（進捗率・残工数・重み・許容幅の設定）に付ける。小数の桁が多い値・整数の部分の桁が多い値は丸めずに 400
（``VALIDATION_ERROR``）にする。誤りの種類と文言は ``Decimal`` の列の入力（``Field(decimal_places=...)``。``schemas.wbs`` など）と同じ
（``decimal_max_places``・``decimal_whole_digits``）。値の桁は、サービス層が保存する値に直すときと同じく ``Decimal(str(値))`` で数える。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AfterValidator
from pydantic_core import PydanticKnownError


def numeric_column(precision: int, scale: int) -> AfterValidator:
    """``Annotated[float | None, numeric_column(10, 2)]`` のように使う。範囲（``ge``・``le``）は ``Field`` で別に書く。"""
    whole_max = precision - scale

    def check(value: float | None) -> float | None:
        if value is None:
            return None
        _sign, digits, exponent = Decimal(str(value)).normalize().as_tuple()
        if not isinstance(exponent, int):  # 無限・非数（allow_inf_nan=False が先に拒む）
            return value
        places = max(-exponent, 0)
        whole = max(len(digits) + exponent, 0)
        if places > scale:
            raise PydanticKnownError("decimal_max_places", {"decimal_places": scale})
        if whole > whole_max:
            raise PydanticKnownError("decimal_whole_digits", {"whole_digits": whole_max})
        return value

    return AfterValidator(check)
