import { AuthProvider } from "./features/auth/hooks/useAuthSession";
import { AppRouter } from "./app/router";

/**
 * 应用根组件
 * 用途：挂载鉴权 Provider 与路由。
 */
function App() {
  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  );
}

export default App;
