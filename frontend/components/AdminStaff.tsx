"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import {
  formatDateTime,
  handoffQueueLabel,
  staffRoleLabel,
  staffStatusLabel,
} from "@/lib/status";
import type {
  AgentPerformance,
  HandoffSettings,
  StaffMember,
} from "@/lib/types";

export function AdminStaff({ currentUserId }: { currentUserId: number }) {
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"owner" | "agent">("agent");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [resetPasswords, setResetPasswords] = useState<Record<number, string>>(
    {},
  );
  const [performance, setPerformance] = useState<AgentPerformance | null>(null);
  const [performanceHours, setPerformanceHours] = useState(168);
  const [performanceBusy, setPerformanceBusy] = useState(false);
  const [performanceError, setPerformanceError] = useState("");
  const [handoffSettings, setHandoffSettings] =
    useState<HandoffSettings | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      setStaff(await api<StaffMember[]>("/api/admin/staff"));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    api<HandoffSettings>("/api/admin/handoff-settings")
      .then(setHandoffSettings)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  const toggleAutoAssign = async (enabled: boolean) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await api<HandoffSettings>(
        "/api/admin/handoff-settings",
        {
          method: "PATCH",
          body: JSON.stringify({ auto_assign_handoffs: enabled }),
        },
      );
      setHandoffSettings(updated);
      setNotice(enabled ? "已开启自动分配" : "已关闭自动分配");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const loadPerformance = useCallback(async () => {
    setPerformanceBusy(true);
    try {
      setPerformance(
        await api<AgentPerformance>(
          `/api/admin/agent-performance?hours=${performanceHours}`,
        ),
      );
      setPerformanceError("");
    } catch (err) {
      setPerformanceError(errorMessage(err));
    } finally {
      setPerformanceBusy(false);
    }
  }, [performanceHours]);

  useEffect(() => {
    void loadPerformance();
  }, [loadPerformance]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<StaffMember>("/api/admin/staff", {
        method: "POST",
        body: JSON.stringify({ email, password, role }),
      });
      setNotice(`员工 ${created.email} 已创建`);
      setEmail("");
      setPassword("");
      setRole("agent");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const update = async (
    member: StaffMember,
    patch: { role?: "owner" | "agent"; status?: "active" | "disabled" },
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/staff/${member.id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      setNotice(`员工 ${member.email} 已更新`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async (member: StaffMember) => {
    const value = resetPasswords[member.id] ?? "";
    if (value.length < 8) {
      setError("新密码至少 8 位");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/staff/${member.id}/password`, {
        method: "POST",
        body: JSON.stringify({ password: value }),
      });
      setNotice(`员工 ${member.email} 密码已重置`);
      setResetPasswords((previous) => ({ ...previous, [member.id]: "" }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const updateSkills = async (member: StaffMember, skills: string[]) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/staff/${member.id}/skills`, {
        method: "PATCH",
        body: JSON.stringify({ skills }),
      });
      setNotice(`员工 ${member.email} 工单技能已更新`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-staff">
      <section
        className="panel panel-pad"
        data-testid="handoff-assignment-settings"
        style={{ marginBottom: 16 }}
      >
        <div className="section-head" style={{ marginBottom: 8 }}>
          <div>
            <h3 style={{ margin: 0 }}>工单分配</h3>
            <p>
              开启后新转人工工单自动分配给心跳在线（90 秒内）、接单中、
              技能匹配且当前处理中工单最少的启用客服；未配置技能视为全部队列；
              无匹配客服时保持待接管。
            </p>
          </div>
        </div>
        <label>
          <input
            type="checkbox"
            data-testid="auto-assign-handoffs"
            checked={handoffSettings?.auto_assign_handoffs ?? false}
            disabled={!handoffSettings || busy}
            onChange={(event) =>
              void toggleAutoAssign(event.target.checked)
            }
          />{" "}
          新工单自动分配
        </label>
      </section>
      <form className="staff-form" onSubmit={create}>
        <div className="field">
          <label htmlFor="staff-email">邮箱</label>
          <input
            id="staff-email"
            data-testid="staff-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            maxLength={255}
          />
        </div>
        <div className="field">
          <label htmlFor="staff-password">初始密码</label>
          <input
            id="staff-password"
            data-testid="staff-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            minLength={8}
            maxLength={128}
          />
        </div>
        <div className="field">
          <label htmlFor="staff-role">角色</label>
          <select
            id="staff-role"
            value={role}
            onChange={(event) =>
              setRole(event.target.value as "owner" | "agent")
            }
          >
            <option value="agent">客服</option>
            <option value="owner">店主</option>
          </select>
        </div>
        <button
          className="btn btn-primary"
          type="submit"
          data-testid="create-staff"
          disabled={busy}
        >
          新增员工
        </button>
      </form>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载团队…</div>
      ) : (
        <div className="admin-grid">
          {staff.map((member) => (
            <article
              className="proposal-card"
              key={member.id}
              data-testid={`staff-${member.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{member.email}</strong>
                  <StatusBadge
                    label={staffRoleLabel(member.role)}
                    tone={member.role === "owner" ? "badge-ok" : "badge-neutral"}
                  />
                  <StatusBadge
                    label={staffStatusLabel(member.status)}
                    status={member.status}
                  />
                  <span
                    data-testid={`staff-online-${member.id}`}
                    className={
                      member.online
                        ? "badge badge-ok"
                        : "badge badge-muted"
                    }
                  >
                    {member.online
                      ? "在线"
                      : member.handoff_available
                        ? "离线"
                        : "已离开"}
                  </span>
                </div>
                <span className="muted">
                  {member.last_seen_at
                    ? `最近心跳 ${formatDateTime(member.last_seen_at)}`
                    : "暂无心跳"}
                </span>
              </div>
              {member.role === "agent" ? (
                <>
                  <div className="proposal-meta">
                    <span>工单技能：</span>
                    {member.handoff_skills.length === 0 ? (
                      <span className="badge badge-neutral">全部队列</span>
                    ) : (
                      member.handoff_skills.map((skill) => (
                        <StatusBadge
                          key={`skill-badge-${member.id}-${skill}`}
                          label={handoffQueueLabel(skill)}
                          tone="badge-neutral"
                        />
                      ))
                    )}
                  </div>
                  <div className="proposal-meta">
                    {(["after_sales", "complaint"] as const).map((queue) => (
                      <label key={`skill-${member.id}-${queue}`}>
                        <input
                          type="checkbox"
                          data-testid={`skill-${member.id}-${queue}`}
                          checked={member.handoff_skills.includes(queue)}
                          disabled={busy}
                          onChange={(event) =>
                            void updateSkills(
                              member,
                              event.target.checked
                                ? [...member.handoff_skills, queue]
                                : member.handoff_skills.filter(
                                    (skill) => skill !== queue,
                                  ),
                            )
                          }
                        />{" "}
                        {handoffQueueLabel(queue)}
                      </label>
                    ))}
                    <span className="muted">
                      都不勾选 = 接收全部队列
                    </span>
                  </div>
                </>
              ) : null}
              <div className="proposal-actions">
                {member.role === "owner" ? (
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    disabled={busy || member.id === currentUserId}
                    onClick={() => void update(member, { role: "agent" })}
                  >
                    降为客服
                  </button>
                ) : (
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    disabled={busy}
                    onClick={() => void update(member, { role: "owner" })}
                  >
                    设为店主
                  </button>
                )}
                {member.status === "active" ? (
                  <button
                    className="btn btn-danger btn-sm"
                    type="button"
                    data-testid={`disable-staff-${member.id}`}
                    disabled={busy || member.id === currentUserId}
                    onClick={() => void update(member, { status: "disabled" })}
                  >
                    停用
                  </button>
                ) : (
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    disabled={busy}
                    onClick={() => void update(member, { status: "active" })}
                  >
                    启用
                  </button>
                )}
                <div className="reject-row">
                  <input
                    type="password"
                    value={resetPasswords[member.id] ?? ""}
                    onChange={(event) =>
                      setResetPasswords((previous) => ({
                        ...previous,
                        [member.id]: event.target.value,
                      }))
                    }
                    placeholder="重置密码（至少 8 位）"
                    maxLength={128}
                    aria-label={`${member.email} 新密码`}
                  />
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    disabled={busy}
                    onClick={() => void resetPassword(member)}
                  >
                    重置密码
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      <section
        className="panel panel-pad"
        data-testid="agent-performance"
        style={{ marginTop: 16 }}
      >
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>坐席绩效</h3>
            <p>
              接管/解决按发生时间计入窗口；SLA
              超时按接管耗时与工单优先级阈值判断。
            </p>
          </div>
          <div className="proposal-actions">
            <select
              value={performanceHours}
              onChange={(event) =>
                setPerformanceHours(Number(event.target.value))
              }
              aria-label="绩效时间窗口"
              data-testid="performance-hours"
            >
              <option value={24}>近 24 小时</option>
              <option value={168}>近 7 天</option>
              <option value={720}>近 30 天</option>
            </select>
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              disabled={performanceBusy}
              onClick={() => void loadPerformance()}
            >
              刷新
            </button>
          </div>
        </div>

        {performanceError ? (
          <div className="notice notice-error">{performanceError}</div>
        ) : null}
        {performanceBusy && !performance ? (
          <div className="loading">加载坐席绩效…</div>
        ) : performance ? (
          <>
            <div className="audit-summary">
              <span>未分配待接管 {performance.unassigned_open}</span>
              <span>
                满意度 {performance.csat_average ?? "—"} 分 /{" "}
                {performance.csat_count} 条
              </span>
              <span className="muted">
                窗口起点 {formatDateTime(performance.since)}
              </span>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>坐席</th>
                  <th>角色</th>
                  <th>待处理</th>
                  <th>窗口内接管</th>
                  <th>窗口内解决</th>
                  <th>平均接管</th>
                  <th>平均解决</th>
                  <th>平均首响</th>
                  <th>首响 SLA 超时</th>
                  <th>接管 SLA 超时</th>
                  <th>解决 SLA 超时</th>
                  <th>满意度</th>
                </tr>
              </thead>
              <tbody>
                {performance.agents.map((row) => (
                  <tr
                    key={row.user_id}
                    data-testid={`performance-row-${row.user_id}`}
                  >
                    <td>
                      {row.email}
                      {row.status === "disabled" ? (
                        <span className="badge badge-muted">已停用</span>
                      ) : null}
                    </td>
                    <td>{staffRoleLabel(row.role)}</td>
                    <td>{row.open_tickets}</td>
                    <td>{row.claimed}</td>
                    <td>{row.resolved}</td>
                    <td>
                      {row.avg_claim_minutes === null
                        ? "—"
                        : `${row.avg_claim_minutes} 分钟`}
                    </td>
                    <td>
                      {row.avg_resolution_minutes === null
                        ? "—"
                        : `${row.avg_resolution_minutes} 分钟`}
                    </td>
                    <td>
                      {row.avg_first_response_minutes === null
                        ? "—"
                        : `${row.avg_first_response_minutes} 分钟`}
                      {row.first_responses > 0
                        ? `（${row.first_responses}）`
                        : ""}
                    </td>
                    <td>
                      <span
                        className={
                          row.first_response_sla_breaches > 0
                            ? "badge badge-danger"
                            : "badge badge-ok"
                        }
                      >
                        {row.first_response_sla_breaches}
                      </span>
                    </td>
                    <td>
                      <span
                        className={
                          row.sla_breaches > 0
                            ? "badge badge-danger"
                            : "badge badge-ok"
                        }
                      >
                        {row.sla_breaches}
                      </span>
                    </td>
                    <td>
                      <span
                        className={
                          row.resolution_sla_breaches > 0
                            ? "badge badge-danger"
                            : "badge badge-ok"
                        }
                      >
                        {row.resolution_sla_breaches}
                      </span>
                    </td>
                    <td>
                      {row.csat_average === null
                        ? "—"
                        : `${row.csat_average} 分`}
                      {row.csat_count > 0
                        ? `（${row.csat_count}）`
                        : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div
              className="section-head"
              style={{ marginTop: 16, marginBottom: 8 }}
            >
              <h4 style={{ margin: 0 }}>最近评价</h4>
            </div>
            {performance.recent_ratings.length === 0 ? (
              <div className="empty-state">窗口内暂无评价。</div>
            ) : (
              <div className="admin-grid">
                {performance.recent_ratings.map((rating) => (
                  <article
                    className="proposal-card"
                    key={rating.ticket_id}
                    data-testid={`recent-rating-${rating.ticket_id}`}
                  >
                    <div className="proposal-head">
                      <div className="proposal-title">
                        <strong>{rating.ticket_ref}</strong>
                        <span
                          className={
                            rating.score <= 2
                              ? "badge badge-danger"
                              : "badge badge-ok"
                          }
                        >
                          {rating.score} 星
                        </span>
                      </div>
                      <span className="muted">
                        {formatDateTime(rating.created_at)}
                      </span>
                    </div>
                    <div className="proposal-meta">
                      <span>坐席 {rating.agent_email ?? "—"}</span>
                      <span>顾客 {rating.customer_phone ?? "—"}</span>
                    </div>
                    {rating.comment ? (
                      <p className="after-sales-reason">{rating.comment}</p>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </>
        ) : null}
      </section>
    </section>
  );
}
