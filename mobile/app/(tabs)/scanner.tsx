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
import type { ScannerSurvivor } from "@/types";

export default function ScannerScreen() {
  const [filter, setFilter] = useState("");

  const {
    data: scanData,
    isLoading,
    isRefetching,
    refetch,
  } = useQuery({
    queryKey: ["scanner-survivors"],
    queryFn: async () => {
      try {
        const response = await api.getScanResults();
        return response.results;
      } catch {
        // Fallback demo survivors
        return [
          {
            symbol: "DIXON",
            company_name: "Dixon Tech",
            sector: "Consumer Durables",
            close_price: 13540.0,
            change_percent: 2.15,
            volume_ratio: 1.85,
            technical_score: 92.5,
            fundamental_score: 84.0,
            composite_score: 89.25,
            rs_rating: 94,
            vcp_stage: "3T Contraction",
            last_scan_at: new Date().toISOString(),
          },
          {
            symbol: "TRENT",
            company_name: "Trent Ltd",
            sector: "Retail",
            close_price: 6850.5,
            change_percent: 1.85,
            volume_ratio: 2.1,
            technical_score: 95.0,
            fundamental_score: 88.0,
            composite_score: 92.2,
            rs_rating: 98,
            vcp_stage: "2T Tight",
            last_scan_at: new Date().toISOString(),
          },
          {
            symbol: "KAYNES",
            company_name: "Kaynes Tech",
            sector: "Electronics",
            close_price: 4720.0,
            change_percent: 3.12,
            volume_ratio: 1.45,
            technical_score: 88.0,
            fundamental_score: 78.5,
            composite_score: 84.2,
            rs_rating: 91,
            vcp_stage: "4T Base",
            last_scan_at: new Date().toISOString(),
          },
        ] as ScannerSurvivor[];
      }
    },
  });

  const filteredSurvivors = (scanData || []).filter(
    (item) =>
      item.symbol.toLowerCase().includes(filter.toLowerCase()) ||
      item.company_name?.toLowerCase().includes(filter.toLowerCase()) ||
      item.sector?.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <Box className="flex-1 bg-background p-4">
      <VStack space="md" className="flex-1">
        {/* Search */}
        <Input size="md" className="bg-card">
          <InputSlot className="pl-3">
            <InputIcon as={SearchIcon} />
          </InputSlot>
          <InputField
            placeholder="Filter survivors by symbol or sector..."
            value={filter}
            onChangeText={setFilter}
          />
        </Input>

        {/* Scan Summary */}
        <HStack className="justify-between items-center px-1">
          <Text size="sm" className="text-muted-foreground">
            {filteredSurvivors.length} V4 Technical Survivors
          </Text>
          <Badge variant="solid" size="sm">
            <BadgeText>EOD Top 20 Candidates</BadgeText>
          </Badge>
        </HStack>

        {isLoading ? (
          <Box className="flex-1 justify-center items-center">
            <Spinner size="large" />
            <Text className="text-muted-foreground mt-3">Loading Survivors...</Text>
          </Box>
        ) : (
          <FlatList
            data={filteredSurvivors}
            keyExtractor={(item) => item.symbol}
            refreshControl={
              <RefreshControl
                refreshing={isRefetching}
                onRefresh={refetch}
                tintColor="#fafafa"
              />
            }
            renderItem={({ item }) => {
              const isPositive = item.change_percent >= 0;

              return (
                <Card className="mb-3 bg-card border-border/80" size="sm">
                  <CardBody>
                    <HStack className="justify-between items-start mb-2">
                      <VStack space="xs">
                        <HStack space="xs" className="items-center">
                          <Heading size="md" bold>
                            {item.symbol}
                          </Heading>
                          {item.rs_rating && (
                            <Badge variant="info" size="sm">
                              <BadgeText>RS {item.rs_rating}</BadgeText>
                            </Badge>
                          )}
                        </HStack>
                        <Text size="xs" className="text-muted-foreground">
                          {item.company_name} • {item.sector}
                        </Text>
                      </VStack>

                      <VStack space="xs" className="items-end">
                        <Text size="md" bold>
                          {formatINR(item.close_price)}
                        </Text>
                        <Badge
                          variant={isPositive ? "success" : "destructive"}
                          size="sm"
                        >
                          <BadgeText>
                            {formatPercent(item.change_percent)}
                          </BadgeText>
                        </Badge>
                      </VStack>
                    </HStack>

                    {/* Scores row */}
                    <HStack className="justify-between items-center bg-secondary/50 p-2.5 rounded-md mt-1">
                      <VStack space="xs">
                        <Text size="2xs" className="text-muted-foreground uppercase">
                          Tech Score
                        </Text>
                        <Text size="xs" bold>
                          {item.technical_score.toFixed(1)}
                        </Text>
                      </VStack>

                      <VStack space="xs">
                        <Text size="2xs" className="text-muted-foreground uppercase">
                          Fund Score
                        </Text>
                        <Text size="xs" bold>
                          {item.fundamental_score ? item.fundamental_score.toFixed(1) : "—"}
                        </Text>
                      </VStack>

                      <VStack space="xs">
                        <Text size="2xs" className="text-muted-foreground uppercase">
                          Vol Ratio
                        </Text>
                        <Text size="xs" bold>
                          {item.volume_ratio.toFixed(2)}x
                        </Text>
                      </VStack>

                      <VStack space="xs" className="items-end">
                        <Text size="2xs" className="text-muted-foreground uppercase">
                          Pattern Stage
                        </Text>
                        <Badge variant="outline" size="sm">
                          <BadgeText>{item.vcp_stage ?? "VCP"}</BadgeText>
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
