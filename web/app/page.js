import { latestBundle } from "@/lib/db";
import Shell from "@/components/Shell";
import Screener from "@/components/Screener";
import Brief from "@/components/Brief";

export const dynamic = "force-dynamic";

export default async function Home() {
  const bundle = await latestBundle();
  const regime = bundle?.run?.regime || null;
  return (
    <Shell regime={regime} asOf={bundle?.run?.run_date?.slice(0, 10)}>
      {bundle
        ? <>
            <Brief brief={bundle.run?.ai_brief} />
            <Screener run={bundle.run} candidates={bundle.candidates} />
          </>
        : (
          <div className="panel">
            <h3>No scan data yet</h3>
            <div className="reasoning">Run the nightly-scan GitHub Action once (Actions tab → nightly-scan → Run workflow), then refresh. First run takes 10–20 minutes.</div>
          </div>
        )}
    </Shell>
  );
}
