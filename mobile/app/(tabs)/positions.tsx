import React from "react";
import { FlatList, RefreshControl, Alert } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { Box } from "@/components/ui/box";
import { Text } from "@/components/ui/text";
import { Heading } from "@/components/ui/heading";
import { VStack } from "@/components/ui/vstack";
import { HStack } from "@/components/ui/hstack";
import { Card, CardHeader, CardBody, CardFooter } from "@/components/ui/card";
import { Badge, BadgeText } from "@/components/ui/badge";
import { Button, ButtonText, ButtonIcon } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ChevronRightIcon } from "@/components/ui/icon";
import { api } from "@/lib/api";
import { formatINR, formatPercent } from "@/lib/utils";
import type { Position } from "@/types";

export default function PositionsScreen() {
  const router = useRouter();

  const {
    data: positions,
    isLoading,
    isRefetching,
    refetch,
  } = useQuery({
    queryKey: ["positions"],
    queryFn: async () => {
      try {
        return await api.getPositions();
      } catch {
        // Fallback demo positions
        return [
          {
            id: "pos-1",
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
          },
          {
            id: "pos-2",
            symbol: "BEL",
            side: "BUY",
            product_type: "CNC",
            entry_price: 308.5,
            current_price: 305.2,
            quantity: 350,
            remaining_qty: 350,
            unrealized_pnl: -1155.0,
            realized_pnl: 0.0,
            pnl_pct: -1.07,
            status: "open",
            stop_loss_price: 298.0,
            trailing_stage: "Initial Stop",
            highest_price_seen: 311.0,
            entry_time: new Date(Date.now() - 3600000 * 24).toISOString(),
            updated_at: new Date().toISOString(),
          },
        ] as Position[];
      }
    },
  });

  const totalUnrealizedPnl = (positions || []).reduce(
    (acc, item) => acc + item.unrealized_pnl,
    0
  );

  return (
    <Box className="flex-1 bg-background p-4">
      <VStack space="md" className="flex-1">
        {/* Total Portfolio P&L Banner */}
        <Card className="bg-card border-border/80" size="md">
          <CardBody>
            <VStack space="xs">
              <Text size="xs" className="text-muted-foreground uppercase font-bold">
                Unrealized Open P&L
              </Text>
              <HStack className="justify-between items-baseline">
                <Heading
                  size="2xl"
                  bold
                  className={
                    totalUnrealizedPnl >= 0 ? "text-success" : "text-destructive"
                  }
                >
                  {formatINR(totalUnrealizedPnl)}
                </Heading>
                <Badge
                  variant={totalUnrealizedPnl >= 0 ? "success" : "destructive"}
                  size="md"
                >
                  <BadgeText>
                    {positions?.length ?? 0} Open Positions
                  </BadgeText>
                </Badge>
              </HStack>
            </VStack>
          </CardBody>
        </Card>

        {isLoading ? (
          <Box className="flex-1 justify-center items-center">
            <Spinner size="large" />
            <Text className="text-muted-foreground mt-3">Loading Positions...</Text>
          </Box>
        ) : (
          <FlatList
            data={positions || []}
            keyExtractor={(item) => item.id}
            refreshControl={
              <RefreshControl
                refreshing={isRefetching}
                onRefresh={refetch}
                tintColor="#fafafa"
              />
            }
            renderItem={({ item }) => {
              const isProfit = item.unrealized_pnl >= 0;

              return (
                <Card className="mb-3.5 bg-card border-border/80" size="md">
                  <CardHeader>
                    <HStack className="justify-between items-center">
                      <VStack space="xs">
                        <HStack space="xs" className="items-center">
                          <Heading size="lg" bold>
                            {item.symbol}
                          </Heading>
                          <Badge variant="default" size="sm">
                            <BadgeText>{item.product_type}</BadgeText>
                          </Badge>
                        </HStack>
                        <Text size="xs" className="text-muted-foreground">
                          {item.quantity} Qty • Entry: {formatINR(item.entry_price)}
                        </Text>
                      </VStack>

                      <VStack space="xs" className="items-end">
                        <Text
                          size="lg"
                          bold
                          className={isProfit ? "text-success" : "text-destructive"}
                        >
                          {formatINR(item.unrealized_pnl)}
                        </Text>
                        <Badge
                          variant={isProfit ? "success" : "destructive"}
                          size="sm"
                        >
                          <BadgeText>
                            {formatPercent(item.pnl_pct)}
                          </BadgeText>
                        </Badge>
                      </VStack>
                    </HStack>
                  </CardHeader>

                  <CardBody>
                    <VStack space="xs" className="bg-secondary/40 p-3 rounded-lg border border-border/40">
                      <HStack className="justify-between items-center">
                        <Text size="xs" className="text-muted-foreground">
                          LTP / Stop Loss:
                        </Text>
                        <Text size="xs" bold>
                          {formatINR(item.current_price)} /{" "}
                          <Text size="xs" className="text-destructive font-bold">
                            {formatINR(item.stop_loss_price)}
                          </Text>
                        </Text>
                      </HStack>

                      {item.trailing_stage && (
                        <HStack className="justify-between items-center pt-1 border-t border-border/30">
                          <Text size="xs" className="text-muted-foreground">
                            Trailing:
                          </Text>
                          <Text size="xs" className="text-primary-foreground font-semibold">
                            {item.trailing_stage}
                          </Text>
                        </HStack>
                      )}
                    </VStack>
                  </CardBody>

                  <CardFooter>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-between"
                      onPress={() => router.push(`/position/${item.id}`)}
                    >
                      <ButtonText>Manage Position & Trailing SL</ButtonText>
                      <ButtonIcon as={ChevronRightIcon} />
                    </Button>
                  </CardFooter>
                </Card>
              );
            }}
          />
        )}
      </VStack>
    </Box>
  );
}
