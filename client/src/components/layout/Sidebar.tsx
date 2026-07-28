import React from 'react';
import {
  LayoutDashboard,
  CandlestickChart,
  Briefcase,
  Receipt,
  BookCheck,
  BookOpen,
  Wallet,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';

export type NavTab =
  | 'overview'
  | 'chart'
  | 'positions'
  | 'orders'
  | 'tradebook'
  | 'journal'
  | 'ledger';

interface SidebarProps {
  activeTab: NavTab;
  setActiveTab: (tab: NavTab) => void;
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  open,
  setOpen,
}) => {
  const navItems: { id: NavTab; label: string; icon: React.FC<{ className?: string }> }[] = [
    { id: 'overview', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'chart', label: 'Chart & Scanner', icon: CandlestickChart },
    { id: 'positions', label: 'Active Positions', icon: Briefcase },
    { id: 'orders', label: 'Orders Book', icon: Receipt },
    { id: 'tradebook', label: 'Tradebook', icon: BookCheck },
    { id: 'journal', label: 'Trade Journal & AI', icon: BookOpen },
    { id: 'ledger', label: 'Account Ledger', icon: Wallet },
  ];

  return (
    <aside
      className={`bg-[#0d1117] border-r border-[#252932] flex flex-col justify-between shrink-0 transition-all duration-200 z-20 ${
        open ? 'w-52' : 'w-14'
      }`}
    >
      <div className="py-2 flex flex-col gap-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex items-center gap-3 px-3.5 py-2.5 mx-1.5 rounded transition-all text-xs font-medium ${
                isActive
                  ? 'bg-[#1c2128] text-[#3b82f6] border-l-2 border-[#3b82f6]'
                  : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#e6edf3]'
              }`}
              title={!open ? item.label : undefined}
            >
              <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-[#3b82f6]' : ''}`} />
              {open && <span className="truncate">{item.label}</span>}
            </button>
          );
        })}
      </div>

      {/* Collapse Toggle Footer */}
      <div className="p-2 border-t border-[#252932]">
        <button
          onClick={() => setOpen(!open)}
          className="w-full flex items-center justify-center p-1.5 hover:bg-[#161b22] rounded text-[#8b949e] hover:text-[#e6edf3] transition-colors"
        >
          {open ? (
            <div className="flex items-center gap-2 text-xs">
              <ChevronLeft className="w-4 h-4" />
              <span>Collapse Sidebar</span>
            </div>
          ) : (
            <ChevronRight className="w-4 h-4" />
          )}
        </button>
      </div>
    </aside>
  );
};
