import type { Metadata } from "next";
import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { AssessmentsView } from "@/components/assessments/AssessmentsView";
import { PublicFooter } from "@/components/site/PublicFooter";
import { PublicHeader } from "@/components/site/PublicHeader";
import { SkipToContent } from "@/components/site/SkipToContent";
import { authOptions } from "@/lib/auth/options";

export const metadata: Metadata = {
  title: "My assessments",
};

export default async function AssessmentsPage(): Promise<JSX.Element> {
  // D-4: guard server-side so an unauthenticated visitor is bounced to sign-in
  // (with a callback back here) instead of a client-side error after paint.
  const session = await getServerSession(authOptions);
  if (!session) {
    redirect("/sign-in?callbackUrl=/assessments");
  }

  return (
    <>
      <SkipToContent />
      <PublicHeader />
      <main id="main-content" className="mx-auto max-w-6xl px-6 py-10">
        <AssessmentsView />
      </main>
      <PublicFooter />
    </>
  );
}
