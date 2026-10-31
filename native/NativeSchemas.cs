using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace NexusAndroid;

/// <summary>Wire contracts exported from the reference server, independently checked by differential tests.</summary>
public static class NativeSchemas
{
    private static readonly (string Key,Func<double,double,bool> Outside)[] Bounds=[("minimum",(n,b)=>n<b),("maximum",(n,b)=>n>b),("exclusiveMinimum",(n,b)=>n<=b),("exclusiveMaximum",(n,b)=>n>=b)];
    private static bool LengthAllowed(JsonObject schema,int length)=>!(schema["minLength"] is JsonNode min&&length<min.GetValue<int>())&&!(schema["maxLength"] is JsonNode max&&length>max.GetValue<int>());
    public static JsonArray Tools()
    {
        using var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("tool-contracts.json")!;
        return JsonNode.Parse(stream)!.AsArray();
    }

    public static void Validate(string name,JsonObject args)
    {
        var tool=Tools().OfType<JsonObject>().SingleOrDefault(t=>t["name"]?.ToString()==name)??throw new ArgumentException("Unknown tool: "+name);
        var root=tool["inputSchema"]!.AsObject();
        void Check(JsonNode? value,JsonObject schema,string path)
        {
            if(schema["$ref"]?.ToString() is { } reference)
            {
                JsonNode? resolved=root;foreach(var part in reference.TrimStart('#','/').Split('/')){resolved=resolved?[part];}Check(value,resolved?.AsObject()??throw new ArgumentException("Unresolved schema reference"),path);return;
            }
            if(schema["anyOf"] is JsonArray alternatives)
            {
                foreach(var option in alternatives.OfType<JsonObject>())try{Check(value,option,path);return;}catch(ArgumentException){/* this alternative does not match: try the next one */}
                throw new ArgumentException(path+": value does not match its allowed types");
            }
            if(schema["enum"] is JsonArray choices&&!choices.Any(c=>c?.ToJsonString()==value?.ToJsonString()))throw new ArgumentException(path+": invalid choice");
            var kind=value?.GetValueKind()??JsonValueKind.Null;
            var type=schema["type"]?.ToString();var valid=type switch
            {
                "null"=>kind==JsonValueKind.Null,"object"=>kind==JsonValueKind.Object,"array"=>kind==JsonValueKind.Array,
                "string"=>kind==JsonValueKind.String,"boolean"=>kind is JsonValueKind.True or JsonValueKind.False,
                "integer"=>kind==JsonValueKind.Number&&decimal.TryParse(value!.ToJsonString(),out var n)&&n==decimal.Truncate(n),
                "number"=>kind==JsonValueKind.Number,_=>true
            };
            if(!valid)throw new ArgumentException(path+": expected "+type);
            if(kind==JsonValueKind.Number)
            {
                var number=double.Parse(value!.ToJsonString(),System.Globalization.CultureInfo.InvariantCulture);if(!double.IsFinite(number))throw new ArgumentException(path+": non-finite number");
                if(Bounds.Any(b=>schema[b.Key] is JsonNode bound&&b.Outside(number,bound.GetValue<double>())))throw new ArgumentException(path+": outside allowed range");
            }
            if(value is JsonValue scalar&&scalar.TryGetValue<string>(out var text)&&!LengthAllowed(schema,text.Length))throw new ArgumentException(path+": invalid text length");
            if(value is JsonObject obj)
            {
                var fields=schema["properties"] as JsonObject;if(schema["required"] is JsonArray required)foreach(var key in required)if(!obj.ContainsKey(key!.ToString()))throw new ArgumentException(path+": required "+key);
                foreach(var pair in obj){if(fields?[pair.Key] is JsonObject child)Check(pair.Value,child,path+"."+pair.Key);else if(schema["additionalProperties"]?.ToJsonString()=="false")throw new ArgumentException(path+": unknown field "+pair.Key);}
            }
            else if(value is JsonArray array)
            {
                if(schema["minItems"] is JsonNode min&&array.Count<min.GetValue<int>()||schema["maxItems"] is JsonNode max&&array.Count>max.GetValue<int>())throw new ArgumentException(path+": invalid array size");
                for(var i=0;i<array.Count;i++){var item=schema["prefixItems"] is JsonArray prefix&&i<prefix.Count?prefix[i] as JsonObject:schema["items"] as JsonObject;if(item is not null)Check(array[i],item,path+"["+i+"]");}
            }
        }
        Check(args,root,name);
    }
}
