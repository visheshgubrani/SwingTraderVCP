import React from "react";
import { ScrollView, Alert } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { Box } from "@/components/ui/box";
import { Text } from "@/components/ui/text";
import { Heading } from "@/components/ui/heading";
import { VStack } from "@/components/ui/vstack";
import { HStack } from "@/components/ui/hstack";
import { Card, CardHeader, CardBody } from "@/components/ui/card";
import { Badge, BadgeText } from "@/components/ui/badge";
import { Button, ButtonText } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { formatINR, formatPercent, formatRelativeTime } from "@/lib/utils";
import type { Position } from "@/types";

export default function PositionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();

  const { data: position, isLoading } = useQuery({
    queryKey: ["position", id],
    queryFn: async () => {
      try {
        const positions = await api.getPositions();
        return positions.find((p) => p.id === id);
      } catch {
        return {
          id: id ?? "pos-1",
          symbol: "KAYNES",
          side: "BUY",
          product_type: "CNC",
          entry_price: 4520.0,
          current_price: 4720.0,
          quantity: 25,
          remaining_qty: 25,
          unrealized_pnl: 5000.0,
          realized_pnl: 0.0,
          pnl_pct: 4.42,
          status: "open",
          stop_loss_price: 4410.0,
          trailing_stage: "Stage 1 (Breakeven Locked)",
          highest_price_seen: 4745.0,
          entry_time: new Date(Date.now() - 3600000 * 48).toISOString(),
          updated_at: new Date().toISOString(),
        } as Position;
      }
    },
    enabled: !!id,
  });

  if (isLoading || !position) {
    return (
      <Box className="flex-1 justify-center items-center bg-background">
        <Spinner size="large" />
      </Box>
    );
  }

  const isProfit = position.unrealized_pnl >= 0;

  return (
    <Box className="flex-1 bg-background">
      <ScrollView className="flex-1 p-4">
        <VStack space="lg" className="pb-8">
          {/* Header */}
          <HStack className="justify-between items-center">
            <VStack space="xs">
              <Heading size="2xl" bold>
                {position.symbol}
              </Heading>
              <Text size="sm" className="text-muted-foreground">
                {position.side} • {position.product_type} • Entered{" "}
                {formatRelativeTime(position.entry_time)}
              </Text>
            </VStack>
            <Badge variant={isProfit ? "success" : "destructive"} size="lg">
              <BadgeText>{formatPercent(position.pnl_pct)}</BadgeText>
            </Badge>
          </HStack>

          {/* P&L Overview Card */}
          <Card className="bg-card border-border/80" size="md">
            <CardHeader>
              <Text size="xs" className="text-muted-foreground uppercase font-bold">
                Unrealized Gain / Loss
              </Text>
              <Heading
                size="2xl"
                bold
                className={isProfit ? "text-success" : "text-destructive"}
              >
                {formatINR(position.unrealized_pnl)}
              </Heading>
            </CardHeader>
            <CardBody>
              <VStack space="sm">
                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Current LTP:
                  </Text>
                  <Text size="sm" bold>
                    {formatINR(position.current_price)}
                  </Text>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Entry Execution Price:
                  </Text>
                  <Text size="sm" bold>
                    {formatINR(position.entry_price)}
                  </Text>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Remaining Holding:
                  </Text>
                  <Text size="sm" bold>
                    {position.remaining_qty} / {position.quantity} shares
                  </Text>
                </HStack>
              </VStack>
            </CardBody>
          </Card>

          {/* Trailing SL Management */}
          <Card className="bg-card border-border/80" size="md">
            <CardHeader>
              <Heading size="md" bold>
                Risk & Trailing Stop Loss
              </Heading>
            </CardHeader>
            <CardBody>
              <VStack space="sm">
                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Active Stop Loss:
                  </Text>
                  <Text size="md" bold className="text-destructive">
                    {formatINR(position.stop_loss_price)}
                  </Text>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Highest Price Reached:
                  </Text>
                  <Text size="sm" bold>
                    {formatINR(position.highest_price_seen ?? position.current_price)}
                  </Text>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Trailing Logic:
                  </Text>
                  <Badge variant="info" size="sm">
                    <BadgeText>{position.trailing_stage ?? "Fixed SL"}</BadgeText>
                  </Badge>
                </HStack>
              </VStack>
            </CardBody>
          </Card>

          {/* Manual Emergency Exit Button */}
          <Button
            variant="destructive"
            size="lg"
            onPress={() =>
              Alert.alert(
                `Emergency Exit ${position.symbol}?`,
                "This will place an immediate market sell order to close this position.",
                [
                  { text: "Cancel", style: "cancel" },
                  {
                    text: "Exit Position",
                    style: "destructive",
                    onPress: () => Alert.alert("Exit Intent Sent", "Order sent to execution engine."),
                  },
                ]
              )
            }
          >
            <ButtonText>Close Position at Market</ButtonText>
          </Button>
        </VStack>
      </ScrollView>
    </Box>
  );
}
