import { useEffect } from "react";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { Home } from "./pages/Home";
import { useAppStore } from "./hooks/useAppStore";

interface AppProps {
  initialTheme?: "light" | "dark";
}

export function App({ initialTheme = "light" }: AppProps) {
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);

  useEffect(() => {
    setTheme(initialTheme);
  }, [initialTheme, setTheme]);

  useEffect(() => {
    const root = document.documentElement;

    if (theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, [theme]);

  return (
    <div className="flex h-full min-h-screen w-full bg-canvas font-sans text-ink">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <Home />
      </div>
    </div>
  );
}