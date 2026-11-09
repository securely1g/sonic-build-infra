int context_middle(void);
int dynamic_side_value(void);

int main(void) {
  return (context_middle() == 8 && dynamic_side_value() == 27) ? 0 : 1;
}
