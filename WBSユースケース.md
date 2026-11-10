# WBSで工数管理する場合のユースケース調査

## エグゼクティブサマリ

WBSを工数管理の基軸にすると、成果物・作業範囲・担当者・予定工数・実績工数・残工数・原価を共通のWBS IDで接続でき、見積、進捗、コスト、リソース管理を統合しやすくなります。PMIはWBSをプロジェクト総スコープの階層的分解として位置づけ、下位のワークパッケージをスケジュール、コスト見積、資源配分、パフォーマンス管理へ接続する基盤としています。citeturn900904search0turn900904search1 国土交通省の教材でも、WBSを用いたトップダウン／ボトムアップ見積と、管理周期を意識したワークパッケージ粒度が解説されています。citeturn900904search6

## 調査レポート

指定いただいた条件を反映した日本語Markdownレポートを作成しました。

**[wbs_effort_usecases.md をダウンロード](sandbox:/mnt/data/wbs_effort_usecases.md)**

レポートには、IT・建設・製造・研究開発・イベント運営について、プロジェクト規模別、請負・準委任・内製といった契約形態別のユースケースを整理しています。また、WBS粒度、WBS辞書、100%ルール、トップダウン／ボトムアップ／類推／PERT・三点見積、バッファ設定、見積→割当→実績→差異分析→是正という運用プロセスをまとめています。PMIは詳細なWBSを使うボトムアップ見積について、ワークパッケージ単位まで分解することで精度を高めやすい一方、見積要素の見落としに注意が必要としています。citeturn900904search2

## ツール・KPI・事例

ツール比較表にはExcel、Microsoft Project、Jira、Backlog、Redmine、工数管理専用SaaS、ERPを掲載しています。Microsoft ProjectではBaseline Work、Actual Work、Remaining Workの比較ができ、Remaining Workは基本的に予定作業量から実績作業量を差し引いた値として扱われます。citeturn118512search0turn118512search1turn118512search2 JiraはOriginal Estimate、Remaining Estimate、Time Spent、Backlogは予定時間・実績時間、RedmineはEstimated/Spent Timeを扱えるため、WBS IDと結び付けることで工数の一貫した追跡が可能です。citeturn118512search3turn118512search4turn118512search5

KPIとしてEAC、予定対実績差異、残工数、稼働率、見積精度、Work Burndown、CPI/SPIなどを整理し、Mermaidによる実務フロー、バーンダウン例、導入ロードマップのガント図も含めています。Microsoft ProjectのWork Burndownは、Baseline、実績、残作業量の推移を比較する用途で提供されています。citeturn118512search6

## 公開事例と実務への示唆

IT分野では、IPAが機能規模による見積とWBS積上げ見積を比較して妥当性を検証する事例を公開しており、異なる見積手法を相互検証する実務パターンとして有用です。citeturn900904search10 建設分野ではWBSとCBSを統合することで、スケジュールとコストを同じ管理構造で扱いやすくなることが研究されています。citeturn744811search6 製造分野の金属加工企業のケーススタディでは、PERT三点見積、Monte Carlo、担当者参加、週次会議の組み合わせにより、対象2案件の時間偏差平均がそれぞれ50%、38%改善したと報告されています。citeturn744811search7 NASAもWBSとWBS辞書を、研究・開発・建設・試験・運用、さらに内製と契約作業の双方でコスト・スケジュール・技術目標を統合する管理基盤として位置づけています。citeturn744811search1turn744811search2

IPAは、WBSが不十分な場合は見積手法だけを改善しても精度向上が難しいこと、仕様変更や欠陥対応などの追加作業を見積に含めないと計画が破綻しやすいことを指摘しています。citeturn900904search7 そのためレポートでは、単純な「予定工数対実績工数」ではなく、**実績工数＋残工数によるEAC予測、成果物ベースの完了基準、BaselineとForecastの分離**を中核の運用原則としています。PMIのソフトウェア開発事例でも、ワークパッケージ単位で資源、予算、マイルストーン、完了基準を定義し、Actual HoursとEstimate to Completeを組み合わせて完了見込みを管理する考え方が示されています。citeturn900904search3