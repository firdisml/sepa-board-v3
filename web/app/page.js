import { latestBundle, latestBacktestStatsByMarket } from "@/lib/db";
import Shell from "@/components/Shell";
import Board from "@/components/Board";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [bundle, btByMarket] = await Promise.all([
    latestBundle(), latestBacktestStatsByMarket(),
  ]);
  const regime = bundle?.run?.regime || null;
  return (
    <Shell regime={regime} asOf={bundle?.run?.run_date?.slice(0, 10)} flush={!!bundle}>
      {bundle
        ? <Board run={bundle.run} candidates={bundle.candidates} regime={regime} btByMarket={btByMarket} />
        : (
          <div className="panel">
            <h3>No scan data yet</h3>
            <div className="reasoning">Run the nightly-scan GitHub Action once (Actions tab → nightly-scan → Run workflow), then refresh. First run takes 10–20 minutes.</div>
          </div>
        )}
    </Shell>
  );
}
