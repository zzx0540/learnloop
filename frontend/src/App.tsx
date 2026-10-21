import { Navigate, Route, Routes } from "react-router-dom";

import { AuthGuard } from "./lib/auth";
import AdminCosts from "./pages/AdminCosts";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import MarketPage from "./pages/MarketPage";
import Register from "./pages/Register";
import RewardsPage from "./pages/RewardsPage";
import CoursePage from "./pages/course/CoursePage";
import ExamPage from "./pages/course/ExamPage";
import GraphTab from "./pages/course/GraphTab";
import KbTab from "./pages/course/KbTab";
import LanguageProfileTab from "./pages/course/LanguageProfileTab";
import LearnPage from "./pages/course/LearnPage";
import OverviewTab from "./pages/course/OverviewTab";
import PlanTab from "./pages/course/PlanTab";
import QaTab from "./pages/course/QaTab";
import ReadingPage from "./pages/course/ReadingPage";
import ReadingTab from "./pages/course/ReadingTab";
import QuizPage from "./pages/course/QuizPage";
import ReviewPage from "./pages/course/ReviewPage";
import VocabTab from "./pages/course/VocabTab";
import WikiTab from "./pages/course/WikiTab";
import WritingPage from "./pages/course/WritingPage";
import WritingTab from "./pages/course/WritingTab";
import DailyNotesPrintPage from "./pages/print/DailyNotesPrintPage";
import OutlinePrintPage from "./pages/print/OutlinePrintPage";
import QuizPrintPage from "./pages/print/QuizPrintPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/"
        element={
          <AuthGuard>
            <Dashboard />
          </AuthGuard>
        }
      />
      <Route
        path="/admin/costs"
        element={
          <AuthGuard>
            <AdminCosts />
          </AuthGuard>
        }
      />
      <Route
        path="/market"
        element={
          <AuthGuard>
            <MarketPage />
          </AuthGuard>
        }
      />
      <Route
        path="/rewards"
        element={
          <AuthGuard>
            <RewardsPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/learn/:itemId"
        element={
          <AuthGuard>
            <LearnPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/review"
        element={
          <AuthGuard>
            <ReviewPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/reading/:readingId"
        element={
          <AuthGuard>
            <ReadingPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/writing/:writingId"
        element={
          <AuthGuard>
            <WritingPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/exam/:quizId"
        element={
          <AuthGuard>
            <ExamPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/quiz/:quizId"
        element={
          <AuthGuard>
            <QuizPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/print/daily-notes"
        element={
          <AuthGuard>
            <DailyNotesPrintPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/print/quiz/:quizId"
        element={
          <AuthGuard>
            <QuizPrintPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId/print/outline"
        element={
          <AuthGuard>
            <OutlinePrintPage />
          </AuthGuard>
        }
      />
      <Route
        path="/courses/:courseId"
        element={
          <AuthGuard>
            <CoursePage />
          </AuthGuard>
        }
      >
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<OverviewTab />} />
        <Route path="plan" element={<PlanTab />} />
        <Route path="vocab" element={<VocabTab />} />
        <Route path="ability" element={<LanguageProfileTab />} />
        <Route path="reading" element={<ReadingTab />} />
        <Route path="writing" element={<WritingTab />} />
        <Route path="graph" element={<GraphTab />} />
        <Route path="qa" element={<QaTab />} />
        <Route path="kb" element={<KbTab />} />
        <Route path="wiki" element={<WikiTab />} />
        <Route path="wiki/:slug" element={<WikiTab />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
