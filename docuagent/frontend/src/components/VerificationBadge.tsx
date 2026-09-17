import type { TaskItem } from "../api";

interface VerificationBadgeProps {
  status?: TaskItem["verification_output"] | null;
}

export function VerificationBadge({ status }: VerificationBadgeProps) {
  if (!status) return null;

  const success = status.returncode === 0;

  return (
    <div
      className="absolute right-4 top-4 flex items-center gap-1.5 rounded-none border px-2 py-1 font-mono text-[10px]"
      style={{
        borderColor: success ? "#6EE7B7" : "#E9A568",
        backgroundColor: success ? "rgba(110, 231, 183, 0.1)" : "rgba(233, 165, 104, 0.1)",
        color: success ? "#6EE7B7" : "#E9A568",
      }}
      title={success ? "验证通过" : "验证失败，点击节点查看详情"}
    >
      <span>{success ? "✓" : "✗"}</span>
      <span className="uppercase tracking-wider">验证</span>
    </div>
  );
}
