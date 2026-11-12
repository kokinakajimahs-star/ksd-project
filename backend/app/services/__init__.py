"""サービス層（設計書 1.3）：ユースケース。1 つの操作を 1 つのトランザクションにし、権限を確かめる。

「今日」の日付はこの層が決めて、ドメイン層へ引数で渡す（NFR-08）。

API 層は基盤（infra）を直接 import しない（1.3：API が依存してよいのはサービスとスキーマ）。
要求ごとの DB のセッションは、ここから ``get_session`` を取って ``Depends`` に渡す。
"""

from app.infra.db import get_session as get_session
