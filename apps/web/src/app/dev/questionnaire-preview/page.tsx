import { getServerSession } from "next-auth";
import { notFound } from "next/navigation";

import { QuestionnairePreview } from "@/components/dev/QuestionnairePreview";
import { authOptions } from "@/lib/auth/options";

/**
 * D-4: the renderer preview is an internal dev tool. Gate it to admins with a
 * server component wrapper — non-admins (and anonymous visitors) get a 404
 * rather than a hint that the route exists.
 */
export default async function QuestionnairePreviewPage(): Promise<JSX.Element> {
  const session = await getServerSession(authOptions);
  if (session?.role !== "admin") {
    notFound();
  }
  return <QuestionnairePreview />;
}
