/**
 * 模块：P10A 本机登录页
 * 用途：required 模式下无会话时的唯一入口；提交后仅内存恢复会话。
 * 对接：useAuthSession.login；POST /api/auth/login
 * 二次开发：禁止把口令写入 storage；勿加第三方字体/图床/图标库。
 */

import { useState, type FormEvent } from "react";
import { useAuthSession } from "../hooks/useAuthSession";
import "./LoginPage.css";

/**
 * 用途：用户名/口令登录表单；未初始化时给出引导说明。
 */
export function LoginPage() {
  const { login, bootstrapped, errorMessage } = useAuthSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const u = username.trim();
    if (!u || !password) {
      setFormError("请输入用户名和口令");
      return;
    }
    setSubmitting(true);
    try {
      await login(u, password);
      // 登录成功后由 AuthProvider 切到业务壳；口令仅存于受控 input
      setPassword("");
    } catch (err) {
      const msg =
        err instanceof Error && err.message ? err.message : "登录失败";
      setFormError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-card__title">本机登录</h1>
        <p className="login-card__desc">
          使用本机账号进入投标工作台。会话由服务端 Cookie 维护，浏览器不保存口令或
          Token。
        </p>

        {!bootstrapped && (
          <div className="login-card__notice" role="status">
            尚未完成管理员引导。请先在后端执行本机管理员引导脚本创建首个所有者账号，再在此登录。
          </div>
        )}

        {(formError || errorMessage) && (
          <div className="login-card__error" role="alert">
            {formError || errorMessage}
          </div>
        )}

        <form className="login-form" onSubmit={onSubmit} autoComplete="off">
          <label className="login-form__field">
            <span>用户名</span>
            <input
              type="text"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting}
              required
            />
          </label>
          <label className="login-form__field">
            <span>口令</span>
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </label>
          <button
            type="submit"
            className="login-form__submit"
            disabled={submitting}
          >
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
