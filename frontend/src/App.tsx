import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AuthGuard } from "./lib/auth";
import { LegalNotice } from "./components/LegalNotice";
const AdminCosts = lazy(() => import("./pages/AdminCosts"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Login = lazy(() => import("./pages/Login"));
const MarketPage = lazy(() => import("./pages/MarketPage"));
const Register = lazy(() => import("./pages/Register"));
const RewardsPage = lazy(() => import("./pages/RewardsPage"));
const CoursePage = lazy(() => import("./pages/course/CoursePage"));
const ExamPage = lazy(() => import("./pages/course/ExamPage"));
const GraphTab = lazy(() => import("./pages/course/GraphTab"));
const KbTab = lazy(() => import("./pages/course/KbTab"));
const LanguageProfileTab = lazy(() => import("./pages/course/LanguageProfileTab"));
const LearnPage = lazy(() => import("./pages/course/LearnPage"));
const OverviewTab = lazy(() => import("./pages/course/OverviewTab"));
const PlanTab = lazy(() => import("./pages/course/PlanTab"));
const QaTab = lazy(() => import("./pages/course/QaTab"));
const ReadingPage = lazy(() => import("./pages/course/ReadingPage"));
const ReadingTab = lazy(() => import("./pages/course/ReadingTab"));
const QuizPage = lazy(() => import("./pages/course/QuizPage"));
const ReviewPage = lazy(() => import("./pages/course/ReviewPage"));
const VocabTab = lazy(() => import("./pages/course/VocabTab"));
const WikiTab = lazy(() => import("./pages/course/WikiTab"));
const WritingPage = lazy(() => import("./pages/course/WritingPage"));
const WritingTab = lazy(() => import("./pages/course/WritingTab"));
const DailyNotesPrintPage = lazy(() => import("./pages/print/DailyNotesPrintPage"));
const OutlinePrintPage = lazy(() => import("./pages/print/OutlinePrintPage"));
const QuizPrintPage = lazy(() => import("./pages/print/QuizPrintPage"));

export default function App() {
  return (
    <>
    <Suspense fallback={<div className="p-8 text-center text-slate-500">正在加载学习空间…</div>}>
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
    </Suspense>
    <LegalNotice />
    </>
  );
}
