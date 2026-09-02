import React from "react";
import { View, Text as RNText, type ViewProps, type TextProps } from "react-native";
import { clsx } from "clsx";

export type CardVariant = "elevated" | "outline" | "filled";
export type CardSize = "sm" | "md" | "lg";

export interface CardProps extends ViewProps {
  variant?: CardVariant;
  size?: CardSize;
  className?: string;
  children?: React.ReactNode;
}

const variantCardClasses: Record<CardVariant, string> = {
  elevated: "bg-card border border-border/60 shadow-sm",
  outline: "bg-transparent border border-border",
  filled: "bg-secondary",
};

const sizeCardClasses: Record<CardSize, string> = {
  sm: "p-3 rounded-lg",
  md: "p-4 rounded-xl",
  lg: "p-6 rounded-2xl",
};

export const Card = React.forwardRef<View, CardProps>(function Card(
  { variant = "elevated", size = "md", className, children, ...props },
  ref
) {
  return (
    <View
      ref={ref}
      className={clsx(
        "overflow-hidden",
        variantCardClasses[variant],
        sizeCardClasses[size],
        className
      )}
      {...props}
    >
      {children}
    </View>
  );
});

Card.displayName = "Card";

export interface CardHeaderProps extends ViewProps {
  className?: string;
  children?: React.ReactNode;
}

export function CardHeader({ className, children, ...props }: CardHeaderProps) {
  return (
    <View className={clsx("flex-col gap-1.5 mb-3", className)} {...props}>
      {children}
    </View>
  );
}

export interface CardBodyProps extends ViewProps {
  className?: string;
  children?: React.ReactNode;
}

export function CardBody({ className, children, ...props }: CardBodyProps) {
  return (
    <View className={clsx("flex-col", className)} {...props}>
      {children}
    </View>
  );
}

export interface CardFooterProps extends ViewProps {
  className?: string;
  children?: React.ReactNode;
}

export function CardFooter({ className, children, ...props }: CardFooterProps) {
  return (
    <View
      className={clsx("flex-row items-center justify-between mt-3 pt-3 border-t border-border/40", className)}
      {...props}
    >
      {children}
    </View>
  );
}

export interface CardTitleProps extends TextProps {
  className?: string;
  children?: React.ReactNode;
}

export function CardTitle({ className, children, ...props }: CardTitleProps) {
  return (
    <RNText
      className={clsx("text-foreground text-lg font-bold", className)}
      {...props}
    >
      {children}
    </RNText>
  );
}

export interface CardDescriptionProps extends TextProps {
  className?: string;
  children?: React.ReactNode;
}

export function CardDescription({
  className,
  children,
  ...props
}: CardDescriptionProps) {
  return (
    <RNText
      className={clsx("text-muted-foreground text-sm", className)}
      {...props}
    >
      {children}
    </RNText>
  );
}
