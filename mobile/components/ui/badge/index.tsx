import React, { createContext, useContext } from "react";
import { View, Text as RNText, type ViewProps, type TextProps } from "react-native";
import { clsx } from "clsx";

export type BadgeVariant =
  | "default"
  | "outline"
  | "solid"
  | "success"
  | "warning"
  | "destructive"
  | "info";

export type BadgeSize = "sm" | "md" | "lg";

interface BadgeContextValue {
  variant: BadgeVariant;
  size: BadgeSize;
}

const BadgeContext = createContext<BadgeContextValue>({
  variant: "default",
  size: "md",
});

export interface BadgeProps extends ViewProps {
  variant?: BadgeVariant;
  size?: BadgeSize;
  className?: string;
  children?: React.ReactNode;
}

const variantBadgeClasses: Record<BadgeVariant, string> = {
  default: "bg-secondary border border-border/50",
  outline: "bg-transparent border border-border",
  solid: "bg-primary",
  success: "bg-success/15 border border-success/30",
  warning: "bg-amber-500/15 border border-amber-500/30",
  destructive: "bg-destructive/15 border border-destructive/30",
  info: "bg-blue-500/15 border border-blue-500/30",
};

const sizeBadgeClasses: Record<BadgeSize, string> = {
  sm: "px-1.5 py-0.5 rounded",
  md: "px-2 py-1 rounded-md",
  lg: "px-2.5 py-1 rounded-md",
};

export const Badge = React.forwardRef<View, BadgeProps>(function Badge(
  { variant = "default", size = "md", className, children, ...props },
  ref
) {
  return (
    <BadgeContext.Provider value={{ variant, size }}>
      <View
        ref={ref}
        className={clsx(
          "flex-row items-center self-start gap-1",
          variantBadgeClasses[variant],
          sizeBadgeClasses[size],
          className
        )}
        {...props}
      >
        {children}
      </View>
    </BadgeContext.Provider>
  );
});

Badge.displayName = "Badge";

export interface BadgeTextProps extends TextProps {
  className?: string;
  children?: React.ReactNode;
}

const variantTextClasses: Record<BadgeVariant, string> = {
  default: "text-foreground font-medium",
  outline: "text-foreground font-medium",
  solid: "text-primary-foreground font-semibold",
  success: "text-success font-semibold",
  warning: "text-amber-400 font-semibold",
  destructive: "text-destructive font-semibold",
  info: "text-blue-400 font-semibold",
};

const sizeTextClasses: Record<BadgeSize, string> = {
  sm: "text-[10px]",
  md: "text-xs",
  lg: "text-sm",
};

export const BadgeText = React.forwardRef<RNText, BadgeTextProps>(
  function BadgeText({ className, children, ...props }, ref) {
    const { variant, size } = useContext(BadgeContext);

    return (
      <RNText
        ref={ref}
        className={clsx(
          variantTextClasses[variant],
          sizeTextClasses[size],
          className
        )}
        {...props}
      >
        {children}
      </RNText>
    );
  }
);

BadgeText.displayName = "BadgeText";

export interface BadgeIconProps {
  as: React.ComponentType<any>;
  size?: number;
  color?: string;
  className?: string;
}

export function BadgeIcon({
  as: IconComponent,
  size,
  color,
  className,
}: BadgeIconProps) {
  const { size: badgeSize } = useContext(BadgeContext);
  const iconSize = size ?? (badgeSize === "sm" ? 10 : badgeSize === "md" ? 12 : 14);

  return <IconComponent size={iconSize} color={color} className={className} />;
}
