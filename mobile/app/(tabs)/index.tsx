import React, { useState } from "react";
import { FlatList, RefreshControl } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { Box } from "@/components/ui/box";
import { Text } from "@/components/ui/text";
import { Heading } from "@/components/ui/heading";
import { VStack } from "@/components/ui/vstack";
import { HStack } from "@/components/ui/hstack";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, BadgeText } from "@/components/ui/badge";
import { Input, InputField, InputSlot, InputIcon } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { SearchIcon } from "@/components/ui/icon";
import { api } from "@/lib/api";
import { formatINR, formatPercent } from "@/lib/utils";
import type { WatchlistItem } from "@/types";

export default function WatchlistScreen() {
  const [search, setSearch] = useState("");

  const {
    data: watchlist,
    isLoading,
    isRefetching,
    refetch,
  } = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => {
      try {
        return await api.getWatchlist();
      } catch {
        // Fallback demo data for initial development when server is disconnected
        return [
          { symbol: "TRENT", company_name: "Trent Ltd", sector: "Retail", ltp: 6850.5, change_pct: 2.35, volume: 1450000 },
          { symbol: "DIXON", company_name: "Dixon Tech", sector: "Electronics", ltp: 13420.0, change_pct: -0.85, volume: 820000 },
          { symbol: "BEL", company_name: "Bharat Electronics", sector: "Defense", ltp: 305.2, change_pct: 1.45, volume: 8900000 },
          { symbol: "KAYNES", company_name: "Kaynes Tech", sector: "Electronics", ltp: 4720.0, change_pct: 3.12, volume: 540000 },
          { symbol: "HAL", company_name: "Hindustan Aeronautics", sector: "Defense", ltp: 4580.0, change_pct: 0.25, volume: 1200000 },
        ] as WatchlistItem[];
      }
    },
  });

  const filteredList = (watchlist || []).filter(
    (item) =>
      item.symbol.toLowerCase().includes(search.toLowerCase()) ||
      item.company_name.toLowerCase().includes(search.toLowerCase()) ||
      item.sector.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Box className="flex-1 bg-background p-4">
      <VStack space="md" className="flex-1">
        {/* Search Bar */}
        <Input size="md" className="bg-card">
          <InputSlot className="pl-3">
            <InputIcon as={SearchIcon} />
          </InputSlot>
          <InputField
            placeholder="Search symbol, company or sector..."
            value={search}
            onChangeText={setSearch}
          />
        </Input>

        {/* Watchlist Counter */}
        <HStack className="justify-between items-center px-1">
          <Text size="sm" className="text-muted-foreground">
            {filteredList.length} Symbols Tracked
          </Text>
          <Badge variant="outline" size="sm">
            <BadgeText>Nifty 500 VCP</BadgeText>
          </Badge>
        </HStack>

        {/* List */}
        {isLoading ? (
          <Box className="flex-1 justify-center items-center">
            <Spinner size="large" />
            <Text className="text-muted-foreground mt-3">Loading Watchlist...</Text>
          </Box>
        ) : (
          <FlatList
            data={filteredList}
            keyExtractor={(item) => item.symbol}
            refreshControl={
              <RefreshControl
                refreshing={isRefetching}
                onRefresh={refetch}
                tintColor="#fafafa"
              />
            }
            renderItem={({ item }) => {
              const isPositive = item.change_pct >= 0;
              return (
                <Card className="mb-2.5 bg-card border-border/70" size="sm">
                  <CardBody>
                    <HStack className="justify-between items-center">
                      <VStack space="xs" className="flex-1">
                        <HStack space="xs" className="items-center">
                          <Heading size="md" bold>
                            {item.symbol}
                          </Heading>
                          <Badge variant="default" size="sm">
                            <BadgeText>{item.sector}</BadgeText>
                          </Badge>
                        </HStack>
                        <Text size="xs" className="text-muted-foreground" isTruncated>
                          {item.company_name}
                        </Text>
                      </VStack>

                      <VStack space="xs" className="items-end">
                        <Text size="md" bold>
                          {formatINR(item.ltp)}
                        </Text>
                        <Badge
                          variant={isPositive ? "success" : "destructive"}
                          size="sm"
                        >
                          <BadgeText>
                            {formatPercent(item.change_pct)}
                          </BadgeText>
                        </Badge>
                      </VStack>
                    </HStack>
                  </CardBody>
                </Card>
              );
            }}
          />
        )}
      </VStack>
    </Box>
  );
}
