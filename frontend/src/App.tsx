import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { Home } from "./pages/Home";
import { LoginPage } from "./pages/LoginPage";
import { SignupPage } from "./pages/SignupPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { ProtectedRoute } from "./components/auth/ProtectedRoute";
import { useAppStore } from "./hooks/useAppStore";
import { useAuthStore } from "./hooks/useAuthStore";

interface AppProps {
  initialTheme?: "light" | "dark";
}

function ChatApp() {
  const initConversations = useAppStore((s) => s.initConversations);

  useEffect(() => {
    initConversations();
  }, [initConversations]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-canvas font-sans text-ink">
      <Sidebar />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col pt-16">
          <Home />
        </div>
      </div>
    </div>
  );
}

export function App({ initialTheme = "light" }: AppProps) {
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);
  const fetchCurrentUser = useAuthStore((s) => s.fetchCurrentUser);

  useEffect(() => {
    setTheme(initialTheme);
  }, [initialTheme, setTheme]);

  // Hydrates auth state from the httpOnly session cookie once, on boot —
  // this is what lets ProtectedRoute tell "not logged in" apart from
  // "still checking" instead of bouncing straight to /login on every reload.
  useEffect(() => {
    fetchCurrentUser();
  }, [fetchCurrentUser]);

  useEffect(() => {
    const root = document.documentElement;

    if (theme === "light") {
      root.classList.add("light");
    } else {
      root.classList.remove("light");
    }
  }, [theme]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <ChatApp />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
