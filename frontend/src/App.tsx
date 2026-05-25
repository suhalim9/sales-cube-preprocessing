import { BrowserRouter, Route, Routes, Navigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Gate } from "@/pages/Gate";
import { Project } from "@/pages/Project";
import { Workspace } from "@/pages/Workspace";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Gate />} />
          <Route path="/project/:slug" element={<Project />} />
          <Route path="/workspace/:slug/:fileId" element={<Workspace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
