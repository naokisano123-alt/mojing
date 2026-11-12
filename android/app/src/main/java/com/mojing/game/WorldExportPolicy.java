package com.mojing.game;

import java.util.List;
import java.util.Map;
import java.util.Arrays;

/** Query policy shared by WebView navigation and Android document export. */
final class WorldExportPolicy {
    private WorldExportPolicy() {}

    static boolean allows(Map<String, List<String>> parameters) {
        if (!Arrays.asList("part", "destination", "journeys").containsAll(parameters.keySet())) return false;
        for (List<String> values : parameters.values()) {
            if (values == null || values.size() != 1 || values.get(0) == null) return false;
        }
        String part = one(parameters, "part");
        if (part == null || !Arrays.asList("world", "saves", "zip").contains(part)) return false;
        String destination = one(parameters, "destination");
        if (destination != null && (!"zip".equals(part) || !"folder".equals(destination))) return false;
        String journeys = one(parameters, "journeys");
        if (journeys != null) {
            if ("world".equals(part)) return false;
            String[] ids = journeys.split(",", -1);
            if (ids.length == 0 || ids.length > 1000) return false;
            for (String id : ids) {
                if (!id.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) return false;
            }
        }
        return true;
    }

    private static String one(Map<String, List<String>> parameters, String key) {
        List<String> values = parameters.get(key);
        return values == null ? null : values.get(0);
    }
}
