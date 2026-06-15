%module(directors="1") features

// Directors make SWIG emit the Go C++ header declared by swig_gen_go.
%feature("director") Feature;
%inline %{
class Feature {
public:
    virtual ~Feature() {}
    virtual int value() { return 1; }
};
%}

// Require both a bare flag and its value to select the enabled interface.
#if defined(ENABLE_FEATURE) && FEATURE_VALUE == 37
%inline %{
int enabled_feature() { return 37; }
%}
#else
%inline %{
int disabled_feature() { return 0; }
%}
#endif
