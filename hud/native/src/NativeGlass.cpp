#include "NativeGlass.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <dwmapi.h>
#include <DispatcherQueue.h>
#include <windows.ui.composition.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Numerics.h>
#include <winrt/Windows.System.h>
#include <winrt/Windows.UI.Composition.Desktop.h>
#include <winrt/Windows.UI.Composition.h>

namespace composition = winrt::Windows::UI::Composition;

namespace {
constexpr wchar_t PaneClass[] = L"JakeHudDesktopGlass";
constexpr DWORD UseHostBackdropBrush = 17;  // DWMWA_USE_HOSTBACKDROPBRUSH
constexpr DWORD WindowCornerPreference = 33;
constexpr DWORD BorderColor = 34;

LRESULT CALLBACK paneProc(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case WM_NCHITTEST:
        return HTTRANSPARENT;  // decorazione: il pannello dell'overlay sopra riceve l'input, mai il vetro
    case WM_MOUSEACTIVATE:
        return MA_NOACTIVATE;
    default:
        return DefWindowProcW(hwnd, message, wParam, lParam);
    }
}

bool registerPaneClass() {
    static const bool registered = [] {
        WNDCLASSEXW wc{};
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = paneProc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = PaneClass;
        return RegisterClassExW(&wc) != 0 || GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
    }();
    return registered;
}

DWORD windowsBuild() {
    using RtlGetVersionFn = LONG(WINAPI *)(OSVERSIONINFOW *);
    static const DWORD build = [] {
        OSVERSIONINFOW info{};
        info.dwOSVersionInfoSize = sizeof(info);
        const auto rtlGetVersion = reinterpret_cast<RtlGetVersionFn>(
            reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion")));
        return rtlGetVersion && rtlGetVersion(&info) == 0 && info.dwMajorVersion >= 10 ? info.dwBuildNumber : DWORD(0);
    }();
    return build;
}

// Il compositore vive sul thread della GUI di Qt (l'unico che crea e muove le lastre) e richiede una DispatcherQueue
// su quel thread; la coda si appoggia ai messaggi di Windows, che il loop di Qt gia' smaltisce. COM e' gia'
// inizializzato da Qt (STA): qui non si reinizializza.
composition::Compositor &compositor() {
    static composition::Compositor instance{nullptr};
    static bool attempted = false;
    if (!attempted) {
        attempted = true;
        try {
            if (!winrt::Windows::System::DispatcherQueue::GetForCurrentThread()) {
                DispatcherQueueOptions options{sizeof(options), DQTYPE_THREAD_CURRENT, DQTAT_COM_NONE};
                ABI::Windows::System::IDispatcherQueueController *controller = nullptr;
                // il controller resta per tutta la vita del processo: la coda serve finche' esiste una lastra
                winrt::check_hresult(CreateDispatcherQueueController(options, &controller));
            }
            instance = composition::Compositor();
        } catch (...) {
            instance = nullptr;
        }
    }
    return instance;
}
} // namespace

struct NativeGlassPane::Impl {
    HWND hwnd = nullptr;
    composition::Desktop::DesktopWindowTarget target{nullptr};
    composition::ContainerVisual root{nullptr};
    composition::CompositionRoundedRectangleGeometry shape{nullptr};
    bool shown = false;
    int x = 0;
    int y = 0;
    int width = -1;
    int height = -1;
    float radius = -1;
};

bool NativeGlassPane::available() {
    static const bool ok = windowsBuild() >= 22000 && registerPaneClass() && compositor() != nullptr;
    return ok;
}

NativeGlassPane::NativeGlassPane() : d(std::make_unique<Impl>()) {
    if (!available())
        return;
    HWND hwnd = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_NOREDIRECTIONBITMAP,
                                PaneClass, L"", WS_POPUP, 0, 0, 1, 1, nullptr, nullptr, GetModuleHandleW(nullptr),
                                nullptr);
    if (!hwnd)
        return;
    const BOOL enable = TRUE;
    const DWORD noRound = 1;               // DWMWCP_DONOTROUND: la forma la da' la geometria, non Windows
    const COLORREF noBorder = 0xFFFFFFFE;  // DWMWA_COLOR_NONE
    DwmSetWindowAttribute(hwnd, WindowCornerPreference, &noRound, sizeof(noRound));
    DwmSetWindowAttribute(hwnd, BorderColor, &noBorder, sizeof(noBorder));
    try {
        winrt::check_hresult(DwmSetWindowAttribute(hwnd, UseHostBackdropBrush, &enable, sizeof(enable)));
        composition::Compositor &c = compositor();
        auto interop = c.as<ABI::Windows::UI::Composition::Desktop::ICompositorDesktopInterop>();
        ABI::Windows::UI::Composition::Desktop::IDesktopWindowTarget *raw = nullptr;
        winrt::check_hresult(interop->CreateDesktopWindowTarget(hwnd, FALSE, &raw));
        composition::Desktop::DesktopWindowTarget target{nullptr};
        winrt::attach_abi(target, raw);

        d->root = c.CreateContainerVisual();
        d->shape = c.CreateRoundedRectangleGeometry();
        d->root.Clip(c.CreateGeometricClip(d->shape));
        auto glass = c.CreateSpriteVisual();
        glass.RelativeSizeAdjustment({1.0f, 1.0f});
        glass.Brush(c.CreateHostBackdropBrush());
        d->root.Children().InsertAtTop(glass);
        target.Root(d->root);
        d->target = target;
        d->hwnd = hwnd;
    } catch (...) {
        DestroyWindow(hwnd);
    }
}

NativeGlassPane::~NativeGlassPane() {
    if (d && d->hwnd) {
        d->target = nullptr;
        DestroyWindow(d->hwnd);
    }
}

bool NativeGlassPane::valid() const {
    return d && d->hwnd;
}

void NativeGlassPane::place(void *insertAfter, int x, int y, int width, int height, float radius, bool restack) {
    if (!valid())
        return;
    const bool moved = x != d->x || y != d->y || width != d->width || height != d->height;
    if (d->shown && !moved && radius == d->radius && !restack)
        return;
    if (width != d->width || height != d->height || radius != d->radius) {
        const winrt::Windows::Foundation::Numerics::float2 size{float(width), float(height)};
        d->root.Size(size);
        d->shape.Size(size);
        d->shape.CornerRadius({radius, radius});
        d->width = width;
        d->height = height;
        d->radius = radius;
    }
    // subito sotto l'overlay, ogni volta: un'altra finestra "sempre sopra" non deve infilarsi fra i due
    SetWindowPos(d->hwnd, static_cast<HWND>(insertAfter), x, y, width, height,
                 SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER);
    d->x = x;
    d->y = y;
    d->shown = true;
}

void NativeGlassPane::hide() {
    if (valid() && d->shown)
        ShowWindow(d->hwnd, SW_HIDE);
    if (d)
        d->shown = false;
}

#else

struct NativeGlassPane::Impl {};
bool NativeGlassPane::available() { return false; }
NativeGlassPane::NativeGlassPane() = default;
NativeGlassPane::~NativeGlassPane() = default;
bool NativeGlassPane::valid() const { return false; }
void NativeGlassPane::place(void *, int, int, int, int, float, bool) {}
void NativeGlassPane::hide() {}

#endif
