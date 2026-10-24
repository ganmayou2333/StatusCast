using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using Microsoft.UI.Xaml.Media;
using StatusCast_Gui.Services;
using Windows.Foundation;
using Windows.UI;

namespace StatusCast_Gui.ViewModels;

public sealed class MainViewModel : INotifyPropertyChanged
{
    private string _modelDisplay = "未知机型";
    private string _batteryDisplay = "🔋 未知";
    private string _mediaDisplay = "🎵 暂无播放";
    private string _mediaTitleDisplay = "暂无播放";
    private string _mediaArtistDisplay = "—";
    private string _stateDisplay = "正在读取状态…";
    private int? _batteryLevel;
    private double _batteryPercent;

    public event PropertyChangedEventHandler? PropertyChanged;

    public string ModelDisplay
    {
        get => _modelDisplay;
        private set => SetField(ref _modelDisplay, value);
    }

    public string BatteryDisplay
    {
        get => _batteryDisplay;
        private set => SetField(ref _batteryDisplay, value);
    }

    public string MediaDisplay
    {
        get => _mediaDisplay;
        private set => SetField(ref _mediaDisplay, value);
    }

    public string MediaTitleDisplay
    {
        get => _mediaTitleDisplay;
        private set => SetField(ref _mediaTitleDisplay, value);
    }

    public string MediaArtistDisplay
    {
        get => _mediaArtistDisplay;
        private set => SetField(ref _mediaArtistDisplay, value);
    }

    public string StateDisplay
    {
        get => _stateDisplay;
        private set => SetField(ref _stateDisplay, value);
    }

    public int? BatteryLevel
    {
        get => _batteryLevel;
        private set
        {
            if (SetField(ref _batteryLevel, value))
            {
                OnPropertyChanged(nameof(BatteryLevelText));
                OnPropertyChanged(nameof(BatteryPercent));
                OnPropertyChanged(nameof(BatteryArcEndPoint));
                OnPropertyChanged(nameof(BatteryArcIsLarge));
                OnPropertyChanged(nameof(BatteryArcColor));
                OnPropertyChanged(nameof(BatteryArcBrush));
                OnPropertyChanged(nameof(BatteryArcGlowBrush));
            }
        }
    }

    public string BatteryLevelText => BatteryLevel?.ToString() ?? "--";

    public double BatteryPercent
    {
        get => _batteryPercent;
        private set
        {
            if (SetField(ref _batteryPercent, value))
            {
                OnPropertyChanged(nameof(BatteryArcEndPoint));
                OnPropertyChanged(nameof(BatteryArcIsLarge));
            }
        }
    }

    public Color BatteryArcColor
    {
        get
        {
            if (BatteryLevel is null)
            {
                return Color.FromArgb(255, 148, 163, 184);
            }

            var level = BatteryLevel.Value;
            if (level < 20)
            {
                return Color.FromArgb(255, 239, 68, 68);
            }

            if (level < 80)
            {
                return Color.FromArgb(255, 34, 211, 238);
            }

            return Color.FromArgb(255, 246, 196, 83);
        }
    }

    public SolidColorBrush BatteryArcBrush => new(BatteryArcColor);

    public SolidColorBrush BatteryArcGlowBrush
    {
        get
        {
            var color = BatteryArcColor;
            return new SolidColorBrush(Color.FromArgb(70, color.R, color.G, color.B));
        }
    }

    public Point BatteryArcEndPoint
    {
        get
        {
            var percent = Math.Clamp(BatteryPercent, 0, 100);
            if (percent >= 100)
            {
                percent = 99.9;
            }

            var radians = percent / 100 * 360 * Math.PI / 180;
            const double center = 130;
            const double radius = 108;
            return new Point(
                center + radius * Math.Sin(radians),
                center - radius * Math.Cos(radians));
        }
    }

    public bool BatteryArcIsLarge => BatteryPercent > 50;

    public void Update(DeviceStatus status)
    {
        ModelDisplay = status.Error is null
            ? status.ModelDisplay
            : "设备未连接";
        BatteryDisplay = status.BatteryDisplay;
        MediaDisplay = status.MediaDisplay;
        MediaTitleDisplay = string.IsNullOrWhiteSpace(status.MediaTitle)
            ? "暂无播放"
            : status.MediaTitle;
        MediaArtistDisplay = string.IsNullOrWhiteSpace(status.MediaArtist)
            ? "—"
            : status.MediaArtist;
        StateDisplay = status.Error ?? $"更新于 {DateTime.Now:HH:mm:ss}";
        BatteryLevel = status.BatteryLevel;
        BatteryPercent = Math.Clamp(status.BatteryLevel ?? 0, 0, 100);
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (Equals(field, value))
        {
            return false;
        }

        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
