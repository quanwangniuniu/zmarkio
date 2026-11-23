import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';

type TooltipSide = 'top' | 'bottom' | 'left' | 'right';

type TooltipProps = {
  content: ReactNode;
  children: ReactNode;
  side?: TooltipSide;
  className?: string;
};

const tooltipSideClasses: Record<TooltipSide, string> = {
  top: 'bottom-full mb-2 left-1/2 -translate-x-1/2',
  bottom: 'top-full mt-2 left-1/2 -translate-x-1/2',
  left: 'right-full mr-2 top-1/2 -translate-y-1/2',
  right: 'left-full ml-2 top-1/2 -translate-y-1/2',
};

export function Tooltip({ content, children, side = 'top', className = '' }: TooltipProps) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      aria-describedby={open ? tooltipId : undefined}
    >
      {children}
      {open ? (
        <span
          id={tooltipId}
          role="tooltip"
          className={`absolute z-50 ${tooltipSideClasses[side]} ${className}`}
        >
          <span className="whitespace-nowrap rounded-md bg-slate-900 px-3 py-1 text-xs text-white shadow-lg">
            {content}
          </span>
        </span>
      ) : null}
    </span>
  );
}

type PopoverProps = {
  trigger: ReactNode;
  title?: string;
  children: ReactNode;
  className?: string;
};

export function Popover({ trigger, title, children, className = '' }: PopoverProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onDocClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="relative inline-flex" ref={rootRef}>
      <span onClick={() => setOpen((prev) => !prev)}>{trigger}</span>
      {open ? (
        <div
          className={`absolute left-1/2 top-full z-50 mt-3 w-64 -translate-x-1/2 rounded-xl border border-slate-200 bg-white p-4 shadow-lg ${className}`}
          role="dialog"
          aria-modal="false"
          aria-labelledby={title ? titleId : undefined}
        >
          {title ? <div id={titleId} className="mb-2 text-sm font-semibold text-slate-900">{title}</div> : null}
          <div className="text-sm text-slate-600">{children}</div>
        </div>
      ) : null}
    </div>
  );
}

type MenuProps = {
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
  onClose?: () => void;
};

type MenuItemRecord = {
  id: string;
  ref: React.RefObject<HTMLButtonElement>;
  disabled: boolean;
  onSelect?: () => void;
};

type MenuContextValue = {
  items: MenuItemRecord[];
  activeIndex: number;
  registerItem: (record: MenuItemRecord) => void;
  unregisterItem: (id: string) => void;
  updateItem: (id: string, updates: Partial<MenuItemRecord>) => void;
  setActiveIndex: (index: number) => void;
  onClose?: () => void;
};

const MenuContext = createContext<MenuContextValue | null>(null);

function getNextEnabledIndex(items: MenuItemRecord[], startIndex: number, direction: 1 | -1) {
  if (items.length === 0) return -1;
  let index = startIndex;
  for (let i = 0; i < items.length; i += 1) {
    index = (index + direction + items.length) % items.length;
    if (!items[index].disabled) return index;
  }
  return -1;
}

export function Menu({ children, className = '', ariaLabel = 'Menu', onClose }: MenuProps) {
  const [items, setItems] = useState<MenuItemRecord[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);

  const registerItem = useCallback((record: MenuItemRecord) => {
    setItems((prev) => [...prev, record]);
  }, []);

  const unregisterItem = useCallback((id: string) => {
    setItems((prev) => {
      const nextItems = prev.filter((item) => item.id !== id);
      setActiveIndex((prevIndex) => {
        if (nextItems.length === 0) return -1;
        if (prevIndex < 0) return prevIndex;
        return Math.min(prevIndex, nextItems.length - 1);
      });
      return nextItems;
    });
  }, []);

  const updateItem = useCallback((id: string, updates: Partial<MenuItemRecord>) => {
    setItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...updates } : item)),
    );
  }, []);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (items.length === 0) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        const nextIndex = getNextEnabledIndex(items, activeIndex, 1);
        if (nextIndex >= 0) {
          items[nextIndex].ref.current?.focus();
          setActiveIndex(nextIndex);
        }
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        const nextIndex = getNextEnabledIndex(items, activeIndex, -1);
        if (nextIndex >= 0) {
          items[nextIndex].ref.current?.focus();
          setActiveIndex(nextIndex);
        }
      }
      if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        items[activeIndex].onSelect?.();
        onClose?.();
      }
    },
    [activeIndex, items, onClose],
  );

  const contextValue = useMemo(
    () => ({
      items,
      activeIndex,
      registerItem,
      unregisterItem,
      updateItem,
      setActiveIndex,
      onClose,
    }),
    [activeIndex, items, onClose, registerItem, unregisterItem, updateItem],
  );

  return (
    <MenuContext.Provider value={contextValue}>
      <div
        role="menu"
        aria-label={ariaLabel}
        className={`min-w-[200px] rounded-xl border border-slate-200 bg-white p-1 shadow-lg ${className}`}
        onKeyDown={onKeyDown}
      >
        {children}
      </div>
    </MenuContext.Provider>
  );
}

type MenuItemProps = {
  children: ReactNode;
  onSelect?: () => void;
  disabled?: boolean;
  description?: string;
};

export function MenuItem({ children, onSelect, disabled = false, description }: MenuItemProps) {
  const menuContext = useContext(MenuContext);
  const id = useId();
  const itemRef = useRef<HTMLButtonElement>(null);
  const items = menuContext?.items ?? [];
  const activeIndex = menuContext?.activeIndex ?? -1;
  const itemIndex = items.findIndex((item) => item.id === id);
  const isActive = itemIndex >= 0 && itemIndex === activeIndex;
  const registerItem = menuContext?.registerItem;
  const unregisterItem = menuContext?.unregisterItem;
  const updateItem = menuContext?.updateItem;
  const setActiveIndex = menuContext?.setActiveIndex;
  const onClose = menuContext?.onClose;

  useEffect(() => {
    if (!registerItem || !unregisterItem) return;
    registerItem({ id, ref: itemRef, disabled, onSelect });
    return () => {
      unregisterItem(id);
    };
  }, [disabled, id, onSelect, registerItem, unregisterItem]);

  useEffect(() => {
    if (!updateItem) return;
    updateItem(id, { disabled, onSelect });
  }, [disabled, id, onSelect, updateItem]);

  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        onSelect?.();
        onClose?.();
      }}
      ref={itemRef}
      tabIndex={disabled ? -1 : isActive ? 0 : -1}
      onFocus={() => {
        if (setActiveIndex && itemIndex >= 0) {
          setActiveIndex(itemIndex);
        }
      }}
      className={`flex w-full items-start justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
        disabled
          ? 'cursor-not-allowed text-slate-300'
          : 'text-slate-700 hover:bg-slate-100 hover:text-slate-900'
      }`}
    >
      <span className="flex flex-col">
        <span className="font-medium">{children}</span>
        {description ? (
          <span className="text-xs text-slate-500">{description}</span>
        ) : null}
      </span>
    </button>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 h-px w-full bg-slate-100" />;
}

type DropdownItem = {
  label: string;
  description?: string;
  disabled?: boolean;
  onSelect?: () => void;
};

type DropdownProps = {
  label: string;
  items: DropdownItem[];
  className?: string;
};

export function Dropdown({ label, items, className = '' }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className={`relative inline-flex ${className}`} ref={rootRef}>
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-400 hover:text-slate-900"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        {label}
        <span className="text-slate-400">▾</span>
      </button>
      {open ? (
        <div className="absolute left-0 top-full z-50 mt-2">
          <Menu ariaLabel={`${label} menu`} onClose={() => setOpen(false)}>
            {items.map((item) => (
              <MenuItem
                key={item.label}
                onSelect={() => {
                  item.onSelect?.();
                }}
                disabled={item.disabled}
                description={item.description}
              >
                {item.label}
              </MenuItem>
            ))}
          </Menu>
        </div>
      ) : null}
    </div>
  );
}
