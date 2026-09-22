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
  const initConversations = useAppStore((s) => s.initConversations);

  useEffect(() => {
    setTheme(initialTheme);
  }, [initialTheme, setTheme]);

  useEffect(() => {
    initConversations();
  }, [initConversations]);

  useEffect(() => {
    const root = document.documentElement;

    if (theme === "light") {
      root.classList.add("light");
    } else {
      root.classList.remove("light");
    }
  }, [theme]);

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