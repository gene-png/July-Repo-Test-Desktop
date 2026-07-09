import type { Metadata } from "next";
import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { ClientServiceDetail } from "@/components/client-services/ClientServiceDetail";
import { PublicFooter } from "@/components/site/PublicFooter";
import { PublicHeader } from "@/components/site/PublicHeader";
import { SkipToContent } from "@/components/site/SkipToContent";
import { authOptions } from "@/lib/auth/options";

import type { JSX } from "react";

export const metadata: Metadata = { title: "Service" };

export default async function ClientServicePage(props: {
  params: Promise<{ serviceId: string }>;
}): Promise<JSX.Element> {
  const params = await props.params;
  const session = await getServerSession(authOptions);
  if (!session) {
    const cb = encodeURIComponent(`/client-services/${params.serviceId}`);
    redirect(`/sign-in?callbackUrl=${cb}`);
  }

  return (
    <>
      <SkipToContent />
      <PublicHeader />
      <main id="main-content" className="mx-auto w-full max-w-4xl px-6 py-10">
        <ClientServiceDetail serviceId={params.serviceId} />
      </main>
      <PublicFooter />
    </>
  );
}
