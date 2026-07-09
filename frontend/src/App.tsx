import { AppRouter } from "./app/router";

/**
 * 应用根组件
 * 用途：挂载路由；全局 Provider（主题/鉴权）后续在此包裹。
 */
function App() {
  return <AppRouter />;
}

export default App;
