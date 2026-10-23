using System;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using StatusCast_Gui.Services;
using StatusCast_Gui.ViewModels;
using Windows.Graphics;

namespace StatusCast_Gui;

public sealed partial class MainWindow : Window
{
    public MainViewModel ViewModel { get; } = new();

    private readonly StatusService _statusService = new();
    private readonly StatusServer _statusServer = new();
    private readonly DispatcherTimer _statusTimer;
    private bool _refreshing;

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);

        AppWindow.SetIcon("Assets/AppIcon.ico");
        AppWindow.Title = "StatusCast";

        var display = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary);
        var workArea = display.WorkArea;
        var width = Math.Clamp((int)(workArea.Width * 0.72), 1200, 1600);
        var height = Math.Clamp((int)(workArea.Height * 0.62), 720, 860);
        AppWindow.Resize(new SizeInt32(width, height));
        AppWindow.Move(new PointInt32(
            workArea.X + (workArea.Width - width) / 2,
            workArea.Y + (workArea.Height - height) / 2));

        _statusTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(30),
        };
        _statusTimer.Tick += OnStatusTimerTick;
        _statusTimer.Start();

        UpdateApiIndicator(_statusServer.Start());
        Closed += (_, _) =>
        {
            _statusTimer.Stop();
            _statusServer.Stop();
        };

        _ = RefreshStatusAsync();
    }

    private async void OnStatusTimerTick(object? sender, object args)
    {
        await RefreshStatusAsync();
    }

    private void UpdateApiIndicator(bool running)
    {
        ApiStatusText.Text = running
            ? $"API: {StatusServer.Port}"
            : "API: 未启动";
        var brush = new SolidColorBrush(
            running ? Microsoft.UI.Colors.LimeGreen : Microsoft.UI.Colors.IndianRed);
        ApiStatusText.Foreground = brush;
        ApiDot.Fill = brush;
    }

    private async Task RefreshStatusAsync()
    {
        if (_refreshing)
        {
            return;
        }

        _refreshing = true;
        try
        {
            var status = await _statusService.ReadAsync();
            DispatcherQueue.TryEnqueue(() => ViewModel.Update(status));
        }
        finally
        {
            _refreshing = false;
        }
    }
}
