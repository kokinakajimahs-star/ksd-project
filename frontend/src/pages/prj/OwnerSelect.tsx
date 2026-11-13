// プロジェクトの責任者（PRJ-01）を選ぶ欄。候補は有効なユーザーの一覧（activeUsers.ts。API-CMN-003）。
// 一覧は user.admin か member.manage が無いと読めない（9.1）ので、読めなければ「必ず出す人」（自分・今の責任者）だけから選ぶ
import type { UserListItem } from "../../api/cmn";
import type { UserSummary } from "../../api/prj";

export function ownerOptions(always: UserSummary[], candidates: UserListItem[] | null): UserSummary[] {
  const options: UserSummary[] = [];
  for (const user of [...always, ...(candidates ?? [])]) {
    if (!options.some((option) => option.id === user.id)) {
      options.push({ id: user.id, login_id: user.login_id, display_name: user.display_name });
    }
  }
  return options;
}

export default function OwnerSelect({
  id,
  value,
  onChange,
  always,
  candidates,
  disabled,
}: {
  id: string;
  value: number;
  onChange: (userId: number) => void;
  /** 一覧を読めなくても選べる人（作る人自身・今の責任者） */
  always: UserSummary[];
  candidates: UserListItem[] | null;
  disabled?: boolean;
}) {
  return (
    <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))}>
      {ownerOptions(always, candidates).map((user) => (
        <option key={user.id} value={user.id}>
          {user.display_name}（{user.login_id}）
        </option>
      ))}
    </select>
  );
}
