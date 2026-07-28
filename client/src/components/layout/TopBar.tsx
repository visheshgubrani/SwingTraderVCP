import React, { useState, useEffect } from 'react';
import {
  LogIn,
  Menu,
  ShieldAlert,
  ShieldCheck,
  Wifi,
  WifiOff,
  Zap,
} from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { useKillSwitch, useSetKillSwitch } from '@/features/admin/api';
import {
  useAuthStatus,
  useStartFyersLogin,
} from '@/features/auth/api';
import type { TickData } from '@/lib/MarketWSContext';

interface TopBarProps {
  sidebarOpen: boolean;
  setSidebarOpen: React.Dispatch<React.SetStateAction<boolean>>;
  activeSymbol?: string;
  activeLtp?: number;
  activeTick?: TickData;
}

export const TopBar: React.FC<TopBarProps> = ({
  sidebarOpen,
  setSidebarOpen,
  activeSymbol = '',
  activeLtp = 0,
  activeTick,
}) => {
  const [timeStr, setTimeStr] = useState<string>('');
  const [isMarketOpen, setIsMarketOpen] = useState<boolean>(true);
  const [killDialogOpen, setKillDialogOpen] = useState(false);
  const killSwitch = useKillSwitch();
  const setKillSwitch = useSetKillSwitch();
  const authStatus = useAuthStatus();
  const startLogin = useStartFyersLogin();
  const killSwitchActive = killSwitch.data?.enabled ?? true;
  const activeChange = activeTick?.change ?? 0;
  const activeChangePct = activeTick?.change_pct ?? 0;

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour12: false,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }) + ' IST'
      );
      
      const hours = now.getHours();
      const mins = now.getMinutes();
      const timeInMins = hours * 60 + mins;
      setIsMarketOpen(timeInMins >= 555 && timeInMins <= 930);
    };

    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleKillSwitchChange = async () => {
    const enabled = !killSwitchActive;
    try {
      await setKillSwitch.mutateAsync({
        enabled,
        reason: enabled
          ? 'Human engaged the global automation kill switch from the UI.'
          : 'Human explicitly resumed automated order handling from the UI.',
      });
      setKillDialogOpen(false);
    } catch {
      // Keep the dialog open; the mutation error is rendered below.
    }
  };

  return (
    <header className="h-12 bg-[#0d1117] border-b border-[#252932] flex items-center justify-between px-3 shrink-0 z-30 select-none">
      {/* Left: Brand & Sidebar Toggle */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="p-1 hover:bg-[#161b22] rounded text-[#8b949e] hover:text-[#e6edf3] transition-colors"
          title="Toggle Navigation Sidebar"
        >
          <Menu className="w-5 h-5" />
        </button>
        
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-[#3b82f6]" />
          <span className="font-bold text-xs tracking-wider text-[#e6edf3]">
            SWINGTRADER <span className="text-[#3b82f6]">VCP</span>
          </span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#161b22] text-[#8b949e] border border-[#252932] font-mono">
            P0–P4 UI
          </span>
        </div>
      </div>

      {/* Center: Active Symbol Ticker Banner */}
      {activeSymbol && (
        <div className="hidden md:flex items-center gap-4 bg-[#080a0e] px-3 py-1 rounded border border-[#252932] font-mono text-xs">
          <span className="font-bold text-[#3b82f6]">{activeSymbol}</span>
          <span className="font-bold text-[#e6edf3]">
            ₹{activeLtp.toFixed(2)}
          </span>
          {activeTick && (
            <>
              <span
                className={`flex items-center font-medium ${
                  activeChange >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'
                }`}
              >
                {activeChange >= 0 ? '+' : ''}
                {activeChange.toFixed(2)} ({activeChangePct >= 0 ? '+' : ''}
                {activeChangePct.toFixed(2)}%)
              </span>
              <span className="text-[11px] text-[#8b949e]">
                O: {activeTick.open?.toFixed(2) ?? '-'} H:{' '}
                {activeTick.high?.toFixed(2) ?? '-'} L:{' '}
                {activeTick.low?.toFixed(2) ?? '-'} V:{' '}
                {activeTick.volume?.toLocaleString('en-IN') ?? '-'}
              </span>
            </>
          )}
        </div>
      )}

      {/* Right: Controls & Kill Switch */}
      <div className="flex items-center gap-3">
        {/* Market Status & Clock */}
        <div className="hidden lg:flex items-center gap-2 text-[11px] font-mono text-[#8b949e]">
          <span className="flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${
                isMarketOpen ? 'bg-[#22c55e] animate-pulse' : 'bg-gray-500'
              }`}
            />
            {isMarketOpen ? 'NSE LIVE' : 'NSE CLOSED'}
          </span>
          <span className="text-[#252932]">|</span>
          <span className="text-[#e6edf3]">{timeStr}</span>
        </div>

        {/* Auth Status */}
        <Button
          disabled={authStatus.isLoading || startLogin.isPending}
          onClick={() => startLogin.mutate()}
          size="sm"
          title={
            authStatus.data?.expires_at
              ? `Token expires ${new Date(authStatus.data.expires_at).toLocaleString('en-IN')}`
              : 'Authenticate with Fyers'
          }
          type="button"
          variant={authStatus.data?.healthy ? 'outline' : 'destructive'}
        >
          {startLogin.isPending ? (
            <Spinner data-icon="inline-start" />
          ) : authStatus.data?.healthy ? (
            <Wifi data-icon="inline-start" />
          ) : authStatus.isError ? (
            <WifiOff data-icon="inline-start" />
          ) : (
            <LogIn data-icon="inline-start" />
          )}
          {authStatus.data?.healthy ? 'FYERS CONNECTED' : 'LOGIN FYERS'}
        </Button>

        {/* Global Kill Switch — persisted in Postgres, not local UI state. */}
        <AlertDialog open={killDialogOpen} onOpenChange={setKillDialogOpen}>
          <AlertDialogTrigger
            render={
              <Button
                disabled={!killSwitch.data || setKillSwitch.isPending}
                size="sm"
                variant={killSwitchActive ? 'destructive' : 'outline'}
              />
            }
          >
            {killSwitch.isLoading || setKillSwitch.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : killSwitchActive ? (
              <ShieldAlert data-icon="inline-start" />
            ) : (
              <ShieldCheck data-icon="inline-start" />
            )}
            {!killSwitch.data
              ? 'CONTROL UNAVAILABLE'
              : killSwitchActive
                ? 'KILL SWITCH ACTIVE'
                : 'ENGINE ENABLED'}
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogMedia>
                {killSwitchActive ? (
                  <ShieldCheck aria-hidden="true" />
                ) : (
                  <ShieldAlert aria-hidden="true" />
                )}
              </AlertDialogMedia>
              <AlertDialogTitle>
                {killSwitchActive
                  ? 'Resume automated orders?'
                  : 'Engage the global kill switch?'}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {killSwitchActive
                  ? 'This re-enables entry confirmation and, in P5, automated exits.'
                  : 'This blocks every new automated entry and exit intent. It does not flatten positions and is not a substitute for being flat.'}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {setKillSwitch.error instanceof Error && (
              <p className="text-sm text-destructive">
                {setKillSwitch.error.message}
              </p>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={setKillSwitch.isPending}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                disabled={setKillSwitch.isPending}
                onClick={() => void handleKillSwitchChange()}
                variant={killSwitchActive ? 'default' : 'destructive'}
              >
                {setKillSwitch.isPending && (
                  <Spinner data-icon="inline-start" />
                )}
                {killSwitchActive ? 'Resume automation' : 'Engage kill switch'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </header>
  );
};
