import { Navigate, Route, Routes } from "react-router";

import { AppLayout } from "./components/AppLayout";
import { RequireAuth } from "./auth/RequireAuth";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PeopleListPage } from "./pages/PeopleListPage";
import { CreatePersonPage, EditPersonPage } from "./pages/PersonFormPage";
import { PersonDetailPage } from "./pages/PersonDetailPage";
import { SignInPage } from "./pages/SignInPage";

/**
 * Public: `/sign-in`. Everything inside `RequireAuth` is private (the guard is for the user's
 * convenience; the API checks every request). `/` goes to the people list.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignInPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/people" replace />} />
          <Route path="/people" element={<PeopleListPage />} />
          <Route path="/people/new" element={<CreatePersonPage />} />
          <Route path="/people/:personId" element={<PersonDetailPage />} />
          <Route path="/people/:personId/edit" element={<EditPersonPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
