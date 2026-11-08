using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace NexusAndroid;

/// <summary>Source-generated JSON metadata for the few typed values the engine serializes (everything else is built as
/// JsonNode). No reflection at run time: the engine publishes with Native AOT, where reflection-based serialization is
/// off, and IsAotCompatible fails the build on any serializer call that would need it.</summary>
[JsonSerializable(typeof(List<DeviceInfo>))]
[JsonSerializable(typeof(string))]
internal sealed partial class EngineJsonContext : JsonSerializerContext;

internal static class EngineJson
{
    /// <summary>Strings written into Codex's config.toml: JSON string syntax (a valid TOML basic string) without escaping
    /// non-ASCII characters or HTML-sensitive ones.</summary>
    private static readonly JsonSerializerOptions Relaxed = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        TypeInfoResolver = EngineJsonContext.Default,
    };

    private static readonly JsonTypeInfo<string> RelaxedString = (JsonTypeInfo<string>)Relaxed.GetTypeInfo(typeof(string));

    internal static string RelaxedQuoted(string text) => JsonSerializer.Serialize(text, RelaxedString);

    internal static System.Text.Json.Nodes.JsonNode? Devices(List<DeviceInfo> devices) =>
        JsonSerializer.SerializeToNode(devices, EngineJsonContext.Default.ListDeviceInfo);
}
