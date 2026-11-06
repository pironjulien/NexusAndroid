using System.Text.Json.Nodes;
using NexusAndroid.Tests.Engine;

namespace NexusAndroid.Tests.Gestures;

/// <summary>Every act gesture on a configured phone (FixtureDriver, no adb): the exact input sent, the guard decision and
/// the reported outcome. The fixture shows "xml_after_input" once an input command ran, like an app answering.</summary>
public sealed class GestureTests
{
    private const string App = "com.example.app";

    private static string Node(string text, string desc, int left, int top, int right, int bottom, string cls = "android.widget.Button", string id = "", string extra = "clickable='true'") =>
        $"<node text='{text}' content-desc='{desc}' resource-id='{id}' package='{App}' class='{cls}' bounds='[{left},{top}][{right},{bottom}]' enabled='true' {extra}/>";

    private static string Screen(params string[] nodes) => "<hierarchy>" + string.Concat(nodes) + "</hierarchy>";

    private static readonly string List = Screen(
        Node("", "", 0, 0, 1080, 1920, "android.widget.ScrollView", "list", "scrollable='true'"),
        Node("Alpha", "", 0, 200, 1080, 400, "android.widget.TextView", "row1", ""),
        Node("Beta", "", 0, 400, 1080, 600, "android.widget.TextView", "row2", ""),
        Node("Open", "", 100, 1500, 500, 1600, id: "open"),
        Node("", "Plus", 900, 100, 1000, 200, "android.widget.ImageButton"));

    private static readonly string Scrolled = Screen(
        Node("", "", 0, 0, 1080, 1920, "android.widget.ScrollView", "list", "scrollable='true'"),
        Node("Gamma", "", 0, 200, 1080, 400, "android.widget.TextView", "row3", ""),
        Node("Open", "", 100, 1500, 500, 1600, id: "open"),
        Node("Menu shown", "", 500, 300, 1000, 400, "android.widget.TextView", "menu", ""),
        Node("", "Plus", 900, 100, 1000, 200, "android.widget.ImageButton"));

    private static JsonObject Fixture(string before, string? after = null)
    {
        var fixture = new JsonObject { ["xml"] = before, ["shell"] = new JsonObject() };
        if (after is not null) fixture["xml_after_input"] = after;
        return fixture;
    }

    private static JsonObject Act(JsonObject fixture, string steps, params (string Tool, JsonObject Args)[] before) =>
        FixtureDriver.Call(new JsonObject { ["tool"] = "act", ["args"] = JsonNode.Parse(steps)!.AsObject(), ["fixture"] = fixture }, [("look", new JsonObject()), .. before]);

    private static string Text(JsonObject result) => string.Join("\n", result["response"]!["result"]!["content"]!.AsArray().Select(b => b!["text"]?.ToString() ?? ""));

    private static List<string> Inputs(JsonObject result) => result["commands"]!.AsArray().Select(c => c!.ToString().Replace("'", "")).Where(c => c.StartsWith("input ", StringComparison.Ordinal)).ToList();

    [Fact]
    public void ScrollDownSwipesUpFromTheCenterAndIsVerifiedByMovedContent()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "scroll", "direction": "down"}]}""");
        Assert.Equal(["input swipe 540 1280 540 640 350"], Inputs(result));
        Assert.Contains("✓ scroll", Text(result));
    }

    [Fact]
    public void ScrollAmountRepeatsTheSwipe()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "scroll", "direction": "up", "amount": 2}]}""");
        Assert.Equal(["input swipe 540 640 540 1280 350", "input swipe 540 640 540 1280 350"], Inputs(result));
    }

    [Fact]
    public void AnAccordAuthorizesOneScrollOnly()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "scroll", "direction": "down", "amount": 3, "accord": "x"}]}""");
        Assert.Empty(Inputs(result));
        Assert.Contains("✗ scroll: An accord authorizes one scroll only", Text(result));
    }

    [Fact]
    public void ScrollRejectsZoomDirections()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "scroll", "direction": "in"}]}""");
        Assert.Empty(Inputs(result));
        Assert.Contains("✗ scroll", Text(result));
    }

    [Fact]
    public void SwipeGoesFromTargetToDestinationIn400Ms()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "swipe", "target": "Beta", "to": "Alpha"}]}""");
        Assert.Equal(["input swipe 540 500 540 300 400"], Inputs(result));
    }

    [Fact]
    public void DragUsesDragAndDropAndIsVerifiedByTheChangedScreen()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "drag", "target": "Beta", "to": "Alpha"}]}""");
        Assert.Equal(["input draganddrop 540 500 540 300 1600"], Inputs(result));
        Assert.Contains("✓ drag", Text(result));
    }

    [Fact]
    public void LongPressHoldsTheSamePoint650Ms()
    {
        var result = Act(Fixture(List, Scrolled), """{"steps": [{"do": "long_press", "target": "Open"}]}""");
        Assert.Equal(["input swipe 300 1550 300 1550 650"], Inputs(result));
        Assert.Contains("✓ long_press", Text(result));
    }

    [Fact]
    public void DoubleTapSendsTwoTapsAfterARecheck()
    {
        var result = Act(Fixture(List, List), """{"steps": [{"do": "double_tap", "target": "Open"}]}""");
        Assert.Equal(["input tap 300 1550", "input tap 300 1550"], Inputs(result));
    }

    [Fact]
    public void DoubleTapIsRefusedOnAnOutwardTrigger()
    {
        var screen = Screen(Node("Envoyer", "", 100, 1500, 500, 1600, id: "go"));
        var result = Act(Fixture(screen, screen), """{"steps": [{"do": "double_tap", "target": "Envoyer", "confirm": true}]}""");
        Assert.Empty(Inputs(result));
        Assert.Contains("✗ double_tap", Text(result));
    }

    [Fact]
    public void ZoomIsRefusedWhenAFingerWouldLandOnAPaymentButton()
    {
        var screen = Screen(Node("Payer 20 €", "", 0, 0, 1080, 1920, id: "pay"));
        var result = Act(Fixture(screen), """{"steps": [{"do": "zoom", "direction": "in"}]}""");
        // The guard already stops it: a payment label under the gesture needs the user's exact agreement.
        Assert.Contains("✗ zoom", Text(result));
    }

    [Fact]
    public void ZoomRejectsScrollDirections()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "zoom", "direction": "down"}]}""");
        Assert.Contains("✗ zoom", Text(result));
    }

    [Fact]
    public void WaitGoneSucceedsAtOnceWhenTheTextIsAbsent()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "wait", "text": "Loading", "gone": true}]}""");
        Assert.Contains("✓ wait condition observed", Text(result));
        Assert.Empty(Inputs(result));
    }

    [Fact]
    public void WaitTimesOutWhenTheTextNeverComes()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "wait", "text": "Never shown", "timeout": 0}]}""");
        Assert.Contains("✗ wait", Text(result));
    }

    [Fact]
    public void AnUnchangedScreenAfterATapIsReportedUnverifiedAndStopsTheBatch()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "tap", "target": "Open"}, {"do": "tap", "target": "Alpha"}]}""");
        Assert.Equal(["input tap 300 1550"], Inputs(result));
        Assert.Contains("~ tap sent, effect unverified", Text(result));
    }

    [Fact]
    public void ARoutineIsSavedWithStableTargetsAndReplayed()
    {
        var fixture = Fixture(List, Scrolled);
        var recorded = """{"steps": [{"do": "tap", "target": "[4]"}], "remember": "open-it"}""";
        var result = Act(fixture, """{"routine": "open-it"}""", ("act", JsonNode.Parse(recorded)!.AsObject()), ("look", new JsonObject()));
        // Replayed on the screen the recording left (the fixture shows no further change): the stable id still aims.
        Assert.DoesNotContain("✗", Text(result));
        Assert.Equal("input tap 300 1550", Inputs(result).Single());
    }

    [Fact]
    public void ADescriptionOnlyTargetIsRememberedByItsDescription()
    {
        var fixture = Fixture(List, Scrolled);
        var recorded = """{"steps": [{"do": "tap", "target": "[5]"}], "remember": "plus"}""";
        var result = Act(fixture, """{"routine": "plus"}""", ("act", JsonNode.Parse(recorded)!.AsObject()), ("look", new JsonObject()));
        Assert.DoesNotContain("✗", Text(result));
        Assert.Equal("input tap 950 150", Inputs(result).Single());
    }

    [Fact]
    public void ARoutineNeedsEveryParameter()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "wait", "text": "{{who}}"}]}""");
        Assert.Contains("Missing routine parameter: who", Text(result));
    }

    [Fact]
    public void ARoutineCannotBeMixedWithSteps()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "tap", "target": "Open"}], "routine": "x"}""");
        Assert.Contains("Use either steps or routine", Text(result));
        Assert.Empty(Inputs(result));
    }

    [Fact]
    public void AnUnknownGestureSendsNothing()
    {
        var result = Act(Fixture(List), """{"steps": [{"do": "tap", "target": "Open"}, {"do": "fly"}]}""");
        Assert.Contains("Error", Text(result));
        Assert.Empty(Inputs(result));
    }

    [Fact]
    public void ALockedPhoneWithACodeGetsNoInput()
    {
        var fixture = Fixture(List, Scrolled);
        fixture["state"] = $"mCurrentFocus=Window{{fixture u0 {App}/.Main}}\nmWakefulness=Awake\nisKeyguardShowing=true";
        fixture["shell"]!["dumpsys trust"] = "(current): deviceLocked=1";
        var result = Act(fixture, """{"steps": [{"do": "tap", "target": "Open"}]}""");
        Assert.Empty(Inputs(result).Where(i => i.StartsWith("input tap", StringComparison.Ordinal)));
    }
}
